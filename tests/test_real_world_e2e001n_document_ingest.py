"""
E2E-001N — PRODUCTION DOCUMENT INGESTION.

WHAT THIS FILE IS

The proof of `POST /projects/{project_id}/documents` and of `reconcile_project_summary`:
that an authenticated project owner can register source files as documents of their own
project, that nobody else can, that content identity and its deduplication are the EXISTING
ones, that a source document is never overwritten, that a failure is reported per file, and
that a project holding several documents never reports a completion its evidence does not
support.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the route and its status mapping, the J19 identity and authorization boundary
              (genuine ES256 tokens against an in-process JWKS document), the ingest
              composition, `create_project_document`'s identity rules, and the aggregation's
              own decision procedure.

    DOUBLED   Storage and the document store, at the seams the module already exposes for
              them. Every double RECORDS what it was asked, so "nothing was uploaded" and
              "no document was created" are asserted over calls that could have happened.

WHAT THIS FILE DOES NOT CLAIM

It touches no real project, uploads nothing, extracts nothing, opens no review, resolves
nothing and creates no artifact. `ARTIFACT_WORKING_DIR` is never read or set.
"""
from __future__ import annotations

import ast
import pathlib
import types

import pytest

from app.production_document_ingest import (
    INGEST_REFUSED_NAME_TAKEN,
    INGEST_REFUSED_UNSAFE_NAME,
    INGEST_REFUSED_UNSUPPORTED_FORMAT,
    UPLOAD_BUCKET,
    ingest_documents,
    upload_object_path,
)

from tests import production_review_auth as auth
from tests import test_real_world_j4_production_extraction_report_truth as j4

production = j4.production

REPO = pathlib.Path(__file__).resolve().parent.parent
MAIN_PATH = REPO / "app" / "main.py"
PIPELINE_PATH = REPO / "app" / "pipeline.py"

PROJECT = "11111111-2222-4333-8444-555555555555"
OWNER = "owner-of-the-project"
SOMEONE_ELSE = "some-other-reviewer"

PDF_BYTES = b"%PDF-1.7\n% " + b"x" * 64
PDF_2_BYTES = b"%PDF-1.7\n% " + b"y" * 64


# ======================================================================================
# Doubles — a storage that records writes, and a store that records documents.
# ======================================================================================
class _Bucket:
    def __init__(self, calls, fail: set[str]):
        self._calls = calls
        self._fail = fail

    def upload(self, path, content, options=None):
        self._calls.append({"path": path, "bytes": len(content), "options": options or {}})
        if path in self._fail:
            raise RuntimeError("storage refused")
        return {"path": path}


class _Storage:
    def __init__(self, calls, fail):
        self._calls = calls
        self._fail = fail

    def from_(self, bucket):
        assert bucket == UPLOAD_BUCKET
        return _Bucket(self._calls, self._fail)


class _StorageClient:
    def __init__(self, fail=None):
        self.calls: list[dict] = []
        self.storage = _Storage(self.calls, fail or set())


class _Store:
    """The document table, with `create_project_document`'s identity rule applied."""

    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.created: list[dict] = []

    def project_documents_for_project(self, project_id):
        return [d for d in self.documents if d["project_id"] == project_id]

    def create_project_document(self, project_id, **fields):
        for existing in self.documents:
            if (
                existing["project_id"] == project_id
                and fields.get("content_sha256")
                and existing.get("content_sha256") == fields["content_sha256"]
            ):
                return existing
        row = {"id": f"doc-{len(self.documents) + 1}", "project_id": project_id, **fields}
        self.documents.append(row)
        self.created.append(row)
        return row


def _hash(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


# ======================================================================================
# A. THE INGEST COMPOSITION
# ======================================================================================
class TestIngestComposition:
    def test_one_file_creates_one_document(self):
        storage, store = _StorageClient(), _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT, files=[("a.pdf", PDF_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert [o["status"] for o in out] == ["created"]
        assert out[0]["document_id"]
        assert len(store.created) == 1

    def test_n_files_create_n_documents(self):
        storage, store = _StorageClient(), _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT,
            files=[("a.pdf", PDF_BYTES), ("b.pdf", PDF_2_BYTES), ("c.dxf", b"0\nSECTION\n")],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert [o["status"] for o in out] == ["created", "created", "created"]
        assert len({o["document_id"] for o in out}) == 3

    def test_the_same_bytes_twice_are_one_document_and_reported_as_deduplicated(self):
        storage, store = _StorageClient(), _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT,
            files=[("a.pdf", PDF_BYTES), ("a.pdf", PDF_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert [o["status"] for o in out] == ["created", "deduplicated"]
        assert out[0]["document_id"] == out[1]["document_id"]
        assert len(store.created) == 1

    def test_the_same_bytes_under_another_name_are_still_one_document(self):
        storage, store = _StorageClient(), _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT,
            files=[("a.pdf", PDF_BYTES), ("renamed.pdf", PDF_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert [o["status"] for o in out] == ["created", "deduplicated"]
        assert out[0]["document_id"] == out[1]["document_id"]
        assert len(storage.calls) == 1

    def test_a_taken_name_is_refused_and_nothing_is_uploaded(self):
        storage = _StorageClient()
        store = _Store([{
            "id": "doc-1", "project_id": PROJECT, "file_name": "a.pdf",
            "content_sha256": "something-else", "storage_path": f"{OWNER}/{PROJECT}/a.pdf",
        }])
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT, files=[("a.pdf", PDF_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert out[0]["status"] == "failed"
        assert out[0]["code"] == INGEST_REFUSED_NAME_TAKEN
        assert storage.calls == []
        assert store.created == []

    def test_no_upload_is_ever_an_upsert(self):
        storage, store = _StorageClient(), _Store()
        ingest_documents(
            owner_id=OWNER, project_id=PROJECT, files=[("a.pdf", PDF_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert storage.calls[0]["options"].get("upsert") is False

    def test_a_storage_failure_is_per_file_and_creates_no_document(self):
        path = upload_object_path(OWNER, PROJECT, "b.pdf")
        storage = _StorageClient(fail={path})
        store = _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT,
            files=[("a.pdf", PDF_BYTES), ("b.pdf", PDF_2_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert [o["status"] for o in out] == ["created", "failed"]
        assert [d["file_name"] for d in store.created] == ["a.pdf"]

    def test_an_unsupported_format_is_refused_before_storage(self):
        storage, store = _StorageClient(), _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT, files=[("notes.txt", b"hello")],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert out[0]["code"] == INGEST_REFUSED_UNSUPPORTED_FORMAT
        assert storage.calls == []

    def test_a_path_in_the_filename_is_refused(self):
        storage, store = _StorageClient(), _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT, files=[("../evil.pdf", PDF_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert out[0]["code"] == INGEST_REFUSED_UNSAFE_NAME
        assert storage.calls == []

    def test_the_storage_path_is_composed_from_the_authorized_owner(self):
        storage, store = _StorageClient(), _Store()
        ingest_documents(
            owner_id=OWNER, project_id=PROJECT, files=[("a.pdf", PDF_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        assert storage.calls[0]["path"] == f"{OWNER}/{PROJECT}/a.pdf"

    def test_no_outcome_carries_a_credential_or_exception_text(self):
        path = upload_object_path(OWNER, PROJECT, "b.pdf")
        storage = _StorageClient(fail={path})
        store = _Store()
        out = ingest_documents(
            owner_id=OWNER, project_id=PROJECT, files=[("b.pdf", PDF_2_BYTES)],
            storage=storage, store=store, hash_rule=_hash,
        )
        blob = repr(out)
        for forbidden in ("service_role", "eyJ", "Bearer", "RuntimeError", "Traceback"):
            assert forbidden not in blob


# ======================================================================================
# B. THE ROUTE — identity, ownership, and the shape of a refused request
# ======================================================================================
class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self._client, self._table, self._filters = client, table, {}
        self._single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        rows = [
            row for row in self._client.rows.get(self._table, [])
            if all(row.get(k) == v for k, v in self._filters.items())
        ]
        if self._single:
            return _Response(rows[0] if rows else None)
        return _Response(rows)


class _ReadOnlyClient:
    def __init__(self, rows):
        self.rows = rows
        self.reads: list = []

    def table(self, name):
        self.reads.append(name)
        return _Query(self, name)


@pytest.fixture
def surface(production, monkeypatch):
    from fastapi.testclient import TestClient

    tokens = auth.install(monkeypatch, production)
    monkeypatch.setattr(
        production.main, "supabase",
        _ReadOnlyClient({"projects": [
            {"id": PROJECT, "user_id": OWNER, "name": "A project", "status": "review"},
        ]}),
    )
    ingested: list = []

    def _ingest(**kwargs):
        ingested.append(kwargs)
        return [{"file_name": "a.pdf", "status": "created", "document_id": "doc-1",
                 "storage_path": f"{OWNER}/{PROJECT}/a.pdf"}]

    monkeypatch.setattr(production.main, "ingest_documents", _ingest)

    return types.SimpleNamespace(
        ingested=ingested,
        url=f"/projects/{PROJECT}/documents",
        http=TestClient(production.main.app, headers=tokens.headers(OWNER)),
        bare=TestClient(production.main.app),
        other=TestClient(production.main.app, headers=tokens.headers(SOMEONE_ELSE)),
        foreign=TestClient(production.main.app, headers={
            "Authorization": f"Bearer {tokens.foreign_token(OWNER)}"}),
        tokens=tokens,
    )


class TestRouteIdentity:
    def test_an_unauthenticated_request_is_refused(self, surface):
        assert surface.bare.post(surface.url, files={"files": ("a.pdf", PDF_BYTES)}).status_code == 401

    def test_a_token_signed_elsewhere_is_refused(self, surface):
        assert surface.foreign.post(surface.url, files={"files": ("a.pdf", PDF_BYTES)}).status_code == 401

    def test_a_reviewer_who_does_not_own_the_project_is_refused(self, surface):
        assert surface.other.post(surface.url, files={"files": ("a.pdf", PDF_BYTES)}).status_code == 403
        assert surface.ingested == []

    def test_an_unknown_project_is_refused(self, surface):
        response = surface.http.post(
            "/projects/22222222-3333-4444-8555-666666666666/documents",
            files={"files": ("a.pdf", PDF_BYTES)},
        )
        assert response.status_code == 404

    def test_the_owner_reaches_the_ingest(self, surface):
        response = surface.http.post(surface.url, files={"files": ("a.pdf", PDF_BYTES)})
        assert response.status_code == 200
        body = response.json()
        assert body["created"] == 1
        assert body["documents"][0]["document_id"] == "doc-1"

    def test_the_owner_passed_to_the_ingest_is_the_authenticated_caller(self, surface):
        surface.http.post(surface.url, files={"files": ("a.pdf", PDF_BYTES)})
        assert surface.ingested[0]["owner_id"] == OWNER
        assert surface.ingested[0]["project_id"] == PROJECT

    def test_a_server_owned_field_is_refused_by_name(self, surface):
        response = surface.http.post(
            surface.url,
            files={"files": ("a.pdf", PDF_BYTES)},
            data={"user_id": "someone-else"},
        )
        assert response.status_code == 422
        assert "user_id" in response.text
        assert surface.ingested == []

    def test_a_request_with_no_files_is_refused_before_any_side_effect(self, surface):
        response = surface.http.post(surface.url)
        assert response.status_code == 422
        assert surface.ingested == []

    def test_no_service_role_key_appears_in_a_response(self, surface):
        body = surface.http.post(surface.url, files={"files": ("a.pdf", PDF_BYTES)}).text
        for forbidden in ("service_role", "eyJ", "SUPABASE_SERVICE_ROLE_KEY"):
            assert forbidden not in body


# ======================================================================================
# C. THE GUARDS THIS WAVE MUST NOT WEAKEN
# ======================================================================================
class TestExistingGuardsSurvive:
    def test_the_route_is_in_the_frozen_ledger(self):
        from tests.test_real_world_j20_connection_review_persistence_gap import FROZEN_ROUTES

        assert (("POST",), "/projects/{project_id}/documents") in FROZEN_ROUTES

    def test_extract_still_accepts_a_document_id(self):
        source = MAIN_PATH.read_text()
        assert "resolve_extraction_source" in source
        assert "document_id" in source

    def test_the_unaddressed_multi_document_guard_is_untouched(self):
        source = (REPO / "app" / "pipeline.py").read_text()
        assert "CONTINUATION_DRAWING_SET_UNRESOLVED" in source
        tree = ast.parse(source)
        guard = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "plan_first_window"
        )
        assert "CONTINUATION_DRAWING_SET_UNRESOLVED" in ast.dump(guard)

    def test_the_ingest_route_creates_no_second_document_authority(self):
        tree = ast.parse(MAIN_PATH.read_text())
        route = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "ingest_project_documents"
        )
        names = {n.attr for n in ast.walk(route) if isinstance(n, ast.Attribute)}
        assert "create_project_document" not in names
        assert "insert" not in names


# ======================================================================================
# D. PROJECT STATUS AGGREGATION
# ======================================================================================
def _states(*run_statuses):
    return [
        {"document_id": f"doc-{i}", "file_name": f"{i}.pdf", "run_status": status}
        for i, status in enumerate(run_statuses)
    ]


class TestAggregation:
    def _reconcile(self, monkeypatch, states, members=(), connections=()):
        import app.pipeline as pipeline

        written: list[dict] = []
        monkeypatch.setattr(pipeline.repo, "document_extraction_states", lambda pid: states)
        monkeypatch.setattr(pipeline.repo, "member_rows_for_project", lambda pid: list(members))
        monkeypatch.setattr(pipeline.repo, "connection_rows_for_project", lambda pid: list(connections))
        monkeypatch.setattr(pipeline.repo, "update_project_summary",
                            lambda pid, **fields: written.append(fields))
        pipeline.reconcile_project_summary(PROJECT)
        return written

    def test_a_single_document_project_is_left_exactly_as_it_was(self, monkeypatch):
        written = self._reconcile(monkeypatch, _states("completed"))
        assert written == []

    def test_two_of_five_done_one_failed_two_processing_never_reports_completion(self, monkeypatch):
        written = self._reconcile(
            monkeypatch,
            _states("completed", "completed", "failed", "running", None),
        )
        assert written[0]["status"] != "done"
        assert written[0]["status"] in ("processing", "failed")

    def test_a_document_with_no_run_yet_is_processing(self, monkeypatch):
        written = self._reconcile(monkeypatch, _states("completed", None))
        assert written[0]["status"] == "processing"

    def test_a_still_running_document_is_processing(self, monkeypatch):
        written = self._reconcile(monkeypatch, _states("completed", "running"))
        assert written[0]["status"] == "processing"

    def test_a_failed_document_never_reports_completion(self, monkeypatch):
        written = self._reconcile(monkeypatch, _states("completed", "failed"))
        assert written[0]["status"] == "failed"

    def test_all_complete_aggregates_the_whole_project_rather_than_the_last_run(
        self, monkeypatch
    ):
        members = [
            {"section_name": "310UB40", "review_status": "extracted", "total_weight_kg": 100.0},
            {"section_name": "200UB25", "review_status": "extracted", "total_weight_kg": 50.0},
        ]
        connections = [{"review_status": "extracted"}, {"review_status": "extracted"}]
        written = self._reconcile(
            monkeypatch, _states("completed", "completed"), members, connections
        )
        assert written[0]["total_members"] == 2
        assert written[0]["total_connections"] == 2
        assert written[0]["total_unique_sections"] == 2
        assert written[0]["total_weight_kg"] == 150.0
        assert written[0]["total_weight_tonnes"] == 0.15

    def test_coverage_that_cannot_be_proven_across_documents_prevents_done(self, monkeypatch):
        """The conservative rule: `done` is unreachable until cross-document coverage exists."""
        members = [{"section_name": "310UB40", "review_status": "extracted", "total_weight_kg": 1.0}]
        written = self._reconcile(monkeypatch, _states("completed", "completed"), members, [])
        assert written[0]["status"] != "done"

    def test_a_review_required_member_keeps_the_project_in_review(self, monkeypatch):
        members = [{"section_name": "310UB40", "review_status": "review_required", "total_weight_kg": 1.0}]
        written = self._reconcile(monkeypatch, _states("completed", "completed"), members, [])
        assert written[0]["status"] == "review"

    def test_no_single_documents_number_is_taken_as_the_projects(self, monkeypatch):
        written = self._reconcile(monkeypatch, _states("completed", "completed"))
        assert written[0]["engineer_reference"] is None
        assert written[0]["structural_engineer"] is None

    def test_the_reconcile_is_called_from_every_project_summary_write(self):
        source = PIPELINE_PATH.read_text()
        # The call sites, counted by their own indentation so the definition line (which
        # starts at column 0 with "def ") is not one of them.
        assert source.count("\n    reconcile_project_summary(project_id)\n") == 3
