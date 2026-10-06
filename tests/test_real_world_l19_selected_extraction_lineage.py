"""
L19 — EXPLICIT SELECTION OF ONE EXTRACTION LINEAGE.

WHAT THIS FILE IS

The evidence for the mechanism L18 proved was missing: a way for a document to SAY which
of its extraction lineages is the one to review, when it has more than one.

The reconstruction's ambiguity refusal is CORRECT and is NOT replaced here. A document with
several readings and no selection still refuses, exactly as it did — this file proves that
first, and proves that a selection is a COMPANION to that guard rather than a way past it.

WHAT IT DELIBERATELY DOES NOT DO

Nothing here selects anything in production. No review is opened, no revision recorded, no
claim taken, no artifact produced, and the L14 lineage is never written to. The tests run
against doubles, and one of them asserts that an automatic process selects NOTHING.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the selection module, its refusal vocabulary, its six validations in order,
              the reconstruction's filter and its refusal to substitute, and the document
              scope it filters within.

    DOUBLED   the database. The doubles record what they were asked, so "a selection was
              written" and "no selection was written" are asserted over calls that could
              have happened.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("SUPABASE_URL", "https://l19-lineage.test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "l19-test-placeholder-not-a-credential")

import pytest  # noqa: E402

from app.production_document_lineage import (  # noqa: E402
    DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID,
    DOCUMENT_LINEAGE_INPUT_REFUSED_SERVER_OWNED_FIELD,
    DOCUMENT_LINEAGE_REFUSED_DOCUMENT_UNKNOWN,
    DOCUMENT_LINEAGE_REFUSED_DRAWING_UNKNOWN,
    DOCUMENT_LINEAGE_REFUSED_FOREIGN_DOCUMENT,
    DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT,
    DOCUMENT_LINEAGE_REFUSED_NO_READING,
    DocumentLineageInputRefused,
    DocumentLineageRefused,
    parse_lineage_selection_request,
    select_document_lineage,
    selected_drawing_id_for_document,
)

P1, P2 = "project-a", "project-b"
DOC_A, DOC_B = "doc-a", "doc-b"


class _Store:
    """A store shaped the way the selection module reads one, recording every call."""

    def __init__(self, *, documents=(), sets=(), drawings=(), captures=(), selected=None):
        self._documents = [dict(row) for row in documents]
        self._sets = [dict(row) for row in sets]
        self._drawings = [dict(row) for row in drawings]
        self._captures = {k: list(v) for k, v in captures.items()}
        self._selected = selected
        self.calls = []
        self.written = []

    def project_documents_for_project(self, project_id):
        self.calls.append(("project_documents_for_project", project_id))
        return [dict(r) for r in self._documents if r["project_id"] == project_id]

    def drawing_sets_for_project(self, project_id):
        self.calls.append(("drawing_sets_for_project", project_id))
        return [dict(r) for r in self._sets if r["project_id"] == project_id]

    def drawing_by_id(self, drawing_id):
        self.calls.append(("drawing_by_id", drawing_id))
        return next((dict(r) for r in self._drawings if r["id"] == drawing_id), None)

    def page_extraction_captures_for_drawing(self, drawing_id):
        self.calls.append(("page_extraction_captures_for_drawing", drawing_id))
        return [dict(r) for r in self._captures.get(drawing_id, [])]

    def selected_drawing_id_for_document(self, document_id):
        self.calls.append(("selected_drawing_id_for_document", document_id))
        return self._selected

    def set_document_selected_drawing(self, document_id, drawing_id):
        self.calls.append(("set_document_selected_drawing", (document_id, drawing_id)))
        self.written.append((document_id, drawing_id))
        self._selected = drawing_id          # the write is visible to the next read
        return {"id": document_id, "selected_drawing_id": drawing_id}


def a_store(**over):
    """One project, two documents, three drawings — the shape the whole file exercises."""
    base = dict(
        documents=[{"id": DOC_A, "project_id": P1}, {"id": DOC_B, "project_id": P1}],
        sets=[{"id": "set-a", "project_id": P1}, {"id": "set-b", "project_id": P1},
              {"id": "set-other-project", "project_id": P2}],
        drawings=[{"id": "drw-a1", "drawing_set_id": "set-a", "document_id": DOC_A},
                  {"id": "drw-a2", "drawing_set_id": "set-a", "document_id": DOC_A},
                  {"id": "drw-b1", "drawing_set_id": "set-b", "document_id": DOC_B},
                  {"id": "drw-elsewhere", "drawing_set_id": "set-other-project",
                   "document_id": DOC_A}],
        captures={"drw-a1": [{"analysis_run_id": "run-a1"}],
                  "drw-a2": [{"analysis_run_id": "run-a2"}],
                  "drw-b1": [{"analysis_run_id": "run-b1"}]},
    )
    base.update(over)
    return _Store(**base)


# ======================================================================================
# 1-7. The selection itself: what it accepts, and what it refuses.
# ======================================================================================
class TestTheSelectionIsExplicitAndScoped:
    def test_a_drawing_of_the_same_document_is_selected(self):
        store = a_store()
        result = select_document_lineage(project_id=P1, document_id=DOC_A,
                                         drawing_id="drw-a1", repository=store)
        assert result["document_id"] == DOC_A
        assert result["selected_drawing_id"] == "drw-a1"
        assert result["selected_drawing_set_id"] == "set-a"
        assert result["selected_analysis_run_ids"] == ["run-a1"]
        assert store.written == [(DOC_A, "drw-a1")]

    def test_a_drawing_of_another_document_is_refused(self):
        store = a_store()
        with pytest.raises(DocumentLineageRefused) as refused:
            select_document_lineage(project_id=P1, document_id=DOC_A,
                                    drawing_id="drw-b1", repository=store)
        assert refused.value.code == DOCUMENT_LINEAGE_REFUSED_FOREIGN_DOCUMENT
        assert store.written == []

    def test_a_drawing_of_another_project_is_refused(self):
        store = a_store()
        with pytest.raises(DocumentLineageRefused) as refused:
            select_document_lineage(project_id=P1, document_id=DOC_A,
                                    drawing_id="drw-elsewhere", repository=store)
        assert refused.value.code == DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT
        assert store.written == []

    def test_a_missing_drawing_is_refused(self):
        store = a_store()
        with pytest.raises(DocumentLineageRefused) as refused:
            select_document_lineage(project_id=P1, document_id=DOC_A,
                                    drawing_id="no-such-drawing", repository=store)
        assert refused.value.code == DOCUMENT_LINEAGE_REFUSED_DRAWING_UNKNOWN
        assert store.written == []

    def test_an_unknown_document_is_refused(self):
        store = a_store()
        with pytest.raises(DocumentLineageRefused) as refused:
            select_document_lineage(project_id=P1, document_id="no-such-document",
                                    drawing_id="drw-a1", repository=store)
        assert refused.value.code == DOCUMENT_LINEAGE_REFUSED_DOCUMENT_UNKNOWN
        assert store.written == []

    def test_a_drawing_with_no_persisted_reading_is_refused(self):
        """A lineage nothing was read from is not one a review could be reconstructed from."""
        store = a_store(captures={"drw-a1": []})
        with pytest.raises(DocumentLineageRefused) as refused:
            select_document_lineage(project_id=P1, document_id=DOC_A,
                                    drawing_id="drw-a1", repository=store)
        assert refused.value.code == DOCUMENT_LINEAGE_REFUSED_NO_READING
        assert store.written == []

    def test_the_request_must_name_a_drawing(self):
        for body in ({}, {"drawing_id": None}, {"drawing_id": "   "}, {"drawing_id": 7}):
            with pytest.raises(DocumentLineageInputRefused) as refused:
                parse_lineage_selection_request(body)
            assert refused.value.code == DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID

    def test_the_request_may_not_name_a_server_owned_field(self):
        with pytest.raises(DocumentLineageInputRefused) as refused:
            parse_lineage_selection_request({"drawing_id": "drw-a1", "document_id": DOC_A})
        assert refused.value.code == DOCUMENT_LINEAGE_INPUT_REFUSED_SERVER_OWNED_FIELD

    def test_selecting_the_same_lineage_twice_writes_the_same_statement(self):
        store = a_store()
        for _ in range(2):
            select_document_lineage(project_id=P1, document_id=DOC_A,
                                    drawing_id="drw-a1", repository=store)
        assert store.written == [(DOC_A, "drw-a1"), (DOC_A, "drw-a1")]


# ======================================================================================
# 1-2, 14. NULL is the ordinary state, and it is never an instruction.
# ======================================================================================
class TestNullIsTheOrdinaryState:
    def test_an_unselected_document_reads_as_none(self):
        store = a_store(selected=None)
        assert selected_drawing_id_for_document(DOC_A, repository=store) is None

    def test_the_read_only_reads(self):
        store = a_store()
        selected_drawing_id_for_document(DOC_A, repository=store)
        assert [name for name, _ in store.calls] == ["selected_drawing_id_for_document"]
        assert store.written == []

    def test_the_column_is_nullable_by_design(self):
        """The migration adds it nullable, with no default and no backfill."""
        import pathlib
        sql = pathlib.Path(
            "supabase/migrations/20261006000000_l19_selected_extraction_lineage.sql"
        ).read_text()
        assert "add column if not exists selected_drawing_id uuid" in sql
        assert "not null" not in sql.split("add column if not exists selected_drawing_id")[1][:80]
        assert "default" not in sql.split("add column if not exists selected_drawing_id")[1][:80]
        assert "update public.project_documents" not in sql.lower()

    def test_none_is_never_read_as_latest_or_first_or_most_complete(self):
        """The read's own source carries no ordering, scoring or choosing."""
        import inspect
        from app.engineering_data import repository as store_module
        source = inspect.getsource(store_module.selected_drawing_id_for_document)
        for word in ("order", "desc", "limit", "latest", "created_at", "status"):
            assert word not in source.lower(), word


# ======================================================================================
# 8-11, 14. The reconstruction: a companion to the guard, never a replacement.
# ======================================================================================
j24a = pytest.importorskip("tests.test_real_world_j24a_revision_zero_producer")


class TestTheReconstructionHonoursTheSelection:
    DOCUMENT = "selby-source-document"

    def _selby(self, *, selected=None, second_lineage=False):
        """The genuine Selby reading, with an OPTIONAL second lineage of the same document.

        A second lineage is a drawing WITH CAPTURES, not merely a row: two drawings and one
        reading between them is not ambiguous, and a fixture that said otherwise would be
        testing the wrong thing.
        """
        store = j24a._selby_store()
        store._drawings = [{
            "id": j24a.SELBY_DRAWING, "drawing_set_id": j24a.SELBY_SET,
            "document_id": self.DOCUMENT, "page_count": j24a.SELBY_PAGE_COUNT,
        }]
        if second_lineage:
            store._drawings.append({
                "id": "a-second-lineage", "drawing_set_id": j24a.SELBY_SET,
                "document_id": self.DOCUMENT, "page_count": j24a.SELBY_PAGE_COUNT,
            })
            j24a.j23._record(
                store._table, j24a._capture_rows_for(j24a.SELBY_WINDOW_FILES[0]),
                run="run-second-lineage", drawing="a-second-lineage",
                drawing_set=j24a.SELBY_SET, project=j24a.SELBY_PROJECT,
            )
        store._selected_drawing_id = selected
        return store

    def _reconstruct(self, store):
        from app.production_review.project_workflow_reconstruction import (
            reconstruct_project_workflow,
        )
        return reconstruct_project_workflow(
            j24a.SELBY_PROJECT, section_matcher=j24a._StubMatcher(),
            repository=store, document_id=self.DOCUMENT,
        )

    def test_an_unselected_document_with_one_reading_reconstructs_normally(self):
        result = self._reconstruct(self._selby())
        assert result is not None
        assert result.workflow.revision == 0

    def test_an_unselected_document_with_two_readings_still_refuses_as_ambiguous(self):
        """THE GUARD, UNCHANGED. L19 is a companion to it and never a way past it."""
        store = self._selby(second_lineage=True)
        with pytest.raises(j24a.producer.ReconstructionRefused) as refused:
            self._reconstruct(store)
        assert refused.value.code == j24a.producer.RECONSTRUCTION_AMBIGUOUS_DOCUMENT

    def test_a_selected_document_with_two_readings_reconstructs_only_the_selected_one(self):
        store = self._selby(selected=j24a.SELBY_DRAWING, second_lineage=True)
        result = self._reconstruct(store)
        assert result.drawing_id == j24a.SELBY_DRAWING

    def test_a_selection_naming_a_drawing_the_document_lacks_fails_closed(self):
        """No fallback, no latest, no nearest — the selection is authoritative."""
        store = self._selby(selected="a-drawing-that-is-not-here")
        with pytest.raises(j24a.producer.ReconstructionRefused) as refused:
            self._reconstruct(store)
        assert refused.value.code == j24a.producer.RECONSTRUCTION_NO_CAPTURE

    def test_clearing_the_selection_restores_the_ambiguity_refusal(self):
        store = self._selby(selected=j24a.SELBY_DRAWING, second_lineage=True)
        assert self._reconstruct(store) is not None

        store._selected_drawing_id = None          # the selection is cleared
        with pytest.raises(j24a.producer.ReconstructionRefused) as refused:
            self._reconstruct(store)
        assert refused.value.code == j24a.producer.RECONSTRUCTION_AMBIGUOUS_DOCUMENT

    def test_the_read_the_selection_needs_is_in_the_reconstruction_read_contract(self):
        assert "selected_drawing_id_for_document" in j24a.ALLOWED_READS


# ======================================================================================
# 12-13. A later extraction never replaces a selection.
# ======================================================================================
class TestNoChronologicalOverride:
    def test_a_newer_extraction_does_not_become_the_selection(self):
        store = a_store()
        select_document_lineage(project_id=P1, document_id=DOC_A,
                                drawing_id="drw-a1", repository=store)
        # A later reading of the same document appears as a new drawing beside drw-a1.
        store._drawings.append({"id": "drw-a3-newer", "drawing_set_id": "set-a",
                                "document_id": DOC_A})
        store._captures["drw-a3-newer"] = [{"analysis_run_id": "run-a3"}]

        assert selected_drawing_id_for_document(DOC_A, repository=store) == "drw-a1"
        assert store.written == [(DOC_A, "drw-a1")]

    def test_a_failed_or_partial_newer_run_does_not_become_the_selection(self):
        store = a_store()
        select_document_lineage(project_id=P1, document_id=DOC_A,
                                drawing_id="drw-a1", repository=store)
        # A newer lineage whose run failed carries no captures at all.
        store._drawings.append({"id": "drw-a4-failed", "drawing_set_id": "set-a",
                                "document_id": DOC_A})
        store._captures["drw-a4-failed"] = []

        assert selected_drawing_id_for_document(DOC_A, repository=store) == "drw-a1"
        assert store.written == [(DOC_A, "drw-a1")]

    def test_nothing_in_the_selection_path_orders_or_ranks_lineages(self):
        import inspect
        from app.production_document_lineage import select_document_lineage as fn
        source = inspect.getsource(fn)
        for word in ("latest", "newest", "most recent", "created_at", "started_at",
                     "completed_at", "order_by", "descending"):
            assert word not in source.lower(), word


# ======================================================================================
# 15. Selecting a lineage produces no review state of any kind.
# ======================================================================================
class TestSelectionWritesNothingElse:
    def test_the_write_is_one_call_and_one_statement(self):
        store = a_store()
        select_document_lineage(project_id=P1, document_id=DOC_A,
                                drawing_id="drw-a1", repository=store)
        writes = [name for name, _ in store.calls if name.startswith("set_")]
        assert writes == ["set_document_selected_drawing"]

    def test_no_review_state_is_reachable_from_the_selection_vocabulary(self):
        import inspect
        from app.production_document_lineage import select_document_lineage as fn
        source = inspect.getsource(fn).lower()
        for word in ("revision", "claim", "artifact", "dispatch", "record_project_review",
                     "working_dir"):
            assert word not in source, word

    def test_the_repository_write_touches_one_column_of_one_table(self):
        import inspect
        from app.engineering_data import repository as store_module
        source = inspect.getsource(store_module.set_document_selected_drawing)
        assert 'table("project_documents")' in source
        assert '"selected_drawing_id"' in source
        assert source.count(".update(") == 1
        assert "insert" not in source and "delete" not in source


# ======================================================================================
# 9. L14 is never selected, by anything, automatically.
# ======================================================================================
L14_DRAWING = "9a06752a-7ae5-46e7-8a29-4fb410b4f5f7"


class TestNoAutomaticSelectionExists:
    def test_no_module_in_the_application_writes_a_selection(self):
        """The ONLY writer is the repository function, and it is called from one place."""
        import pathlib
        writers = sorted(
            str(path) for path in pathlib.Path("app").rglob("*.py")
            if "set_document_selected_drawing" in path.read_text(encoding="utf-8")
            and path.name != "repository.py"
        )
        assert writers == ["app/production_document_lineage.py"], writers

    def test_the_l14_lineage_appears_in_no_automatic_write(self):
        import pathlib
        for path in list(pathlib.Path("app").rglob("*.py")):
            assert L14_DRAWING not in path.read_text(encoding="utf-8"), path

    def test_the_extraction_pipeline_never_selects_a_lineage(self):
        import pathlib
        source = pathlib.Path("app/pipeline.py").read_text(encoding="utf-8")
        assert "selected_drawing" not in source
        assert "set_document_selected_drawing" not in source
