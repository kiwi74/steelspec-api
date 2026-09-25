"""
SteelSpec API — the bridge between the frontend, Supabase, and the
DXF/IFC/PDF parsing engines.

Endpoints:
  POST /extract/{project_id}            — triggers extraction on an uploaded file
  POST /continue-extraction/{project_id} — reads the NEXT window of a PDF
                                          drawing set larger than the page cap
  POST /retry-extraction/{project_id}?page=N
                                        — reads ONE page of a PDF drawing set
                                          again, the one the record names as
                                          having failed to parse
  POST /generate-report/{project_id}    — (re)generates the PDF report on demand
  GET  /health                          — simple healthcheck
  GET  /production/review/{project_id}  — the authenticated production review
                                          surface: one project's page-level
                                          exceptions, read from its persisted
                                          record (Milestone J19)
  POST /production/review/{project_id}/pages/{page_number}/retry
                                        — requests the existing J17 retry for
                                          exactly that page of that project
  /review/...                           — mounted 7AN review UI (internal review
                                          surface; not production-secure)

AUTHENTICATION (Milestone J19)
------------------------------
Every route that reads or mutates a project requires the caller's Supabase Auth
access token, in `Authorization: Bearer <token>`. That is the identity system this
application ALREADY has (the frontend authenticates against it, `projects.user_id`
is a foreign key onto `profiles`, which is the auth user id), so no second
authentication system was created and no credential is invented, configured or
held here. The token is verified against the project's own PUBLIC JWKS — see
`app/production_review/identity.py` — so the only credential involved is a public
key; the JWT signing secret is never read.

A request that presents no verifiable token is refused before it reads anything
(401), and an authenticated caller may only reach a project whose own `user_id` is
their own subject (403) — the same `auth.uid() = user_id` rule the database
already enforces in 37 row-level security policies, restated in code because this
server talks to PostgreSQL with the service-role key, which bypasses RLS.
"""
import os
import tempfile
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import ALLOWED_ORIGINS, SUPABASE_URL
from app.supabase_client import supabase
from app.drawing_reading.dxf_parser import parse_dxf_and_save
from app.pipeline import (
    continue_pdf_extraction,
    parse_pdf_and_save,
    plan_continuation,
    plan_retry,
    retry_pdf_page,
)
from app.validation.page_windows import CONTINUATION_SOURCE_MISMATCH, ContinuationRefused
from app.validation.parse_failures import RetryRefused
from app.report.pdf_generator import generate_report_pdf
from app.export.storage_export import upload_and_record
from app.production_connection_review import build_workflow_review, render_workflow_review
from app.production_review.authorization import authorize_project
from app.production_review.binding import bind_project_review, bound_page_retry, render_bound_review
from app.production_review.identity import (
    IdentityRefused,
    JwksTokenVerifier,
    reviewer_from_authorization,
    supabase_issuer,
    supabase_jwks_url,
)
from app.production_review.project_read import read_project_record
from app.review_ui.web import review_app

app = FastAPI(title="SteelSpec API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 7AO: mount the proven 7AN review UI under the real application as an
# internal review surface. The UI stays a pure consumer (7AJ -> 7AK -> 7AM
# -> pages) and its session layer is a deliberately temporary in-process
# slice with NO authentication — mounting it here does not make it
# production-secure public exposure.
app.mount("/review", review_app)


# ======================================================================================
# The authentication and authorization boundary (Milestone J19).
#
# One dependency, applied to every route that touches a project. It answers exactly
# one question — who is calling — and it does so from the one credential this
# application's existing identity system issues: a Supabase Auth access token,
# verified against this project's own public keys. Nothing else in the request can
# establish an identity, and no route below reads a project before this has run.
# ======================================================================================
_identity_verifier = None


def identity_verifier() -> JwksTokenVerifier:
    """This project's token verifier, built once and reused.

    Lazy on purpose: constructing it fetches nothing, but rebuilding it per request
    would refetch the public key set per request. Tests replace this function, which
    is why nothing else holds a reference to the object it returns.
    """
    global _identity_verifier
    if _identity_verifier is None:
        _identity_verifier = JwksTokenVerifier(supabase_jwks_url(SUPABASE_URL))
    return _identity_verifier


def require_reviewer(authorization: str | None = Header(default=None)):
    """The authenticated reviewer behind this request, or a 401.

    The refusal names itself and carries the rule's own code and reason, and it is
    a STATUS, not a rendered page: "this caller was not identified" is not an
    engineering state of any project, so it is never presented as one.
    """
    try:
        return reviewer_from_authorization(
            authorization,
            verifier=identity_verifier(),
            issuer=supabase_issuer(SUPABASE_URL),
        )
    except IdentityRefused as refused:
        raise HTTPException(
            status_code=401,
            detail={"refusal": refused.code, "reason": refused.detail},
        )


def _authorized_project(project_id: str, reviewer) -> dict:
    """The project row this reviewer may see, or a refusal.

    The rule is `app/production_review/authorization.py`'s, and this function adds
    nothing to it: it loads the row, asks the rule, and refuses with the rule's own
    code. An unknown project is 404; a project this reviewer does not own is 403.
    """
    result = supabase.table("projects").select("*").eq("id", project_id).single().execute()
    project = result.data
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    decision = authorize_project(reviewer, project)
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={"refusal": decision.code, "reason": decision.detail},
        )
    return project


@app.get("/health")
def health():
    return {"status": "ok"}


def build_and_store_report(project_id: str, user_id: str):
    pdf_bytes = generate_report_pdf(project_id)
    report_path = f"{user_id}/{project_id}/steel_schedule.pdf"
    upload_and_record(
        bucket="reports", path=report_path, content=pdf_bytes, content_type="application/pdf",
        table="projects", record_id=project_id, path_column="report_pdf_path",
    )
    return report_path


def run_extraction(project_id: str, storage_path: str, source_format: str, user_id: str):
    """
    Background task: download the file from Supabase Storage, run the
    appropriate parser, write results back to the DB, then generate
    the PDF report. Wrapped in try/except so a failure at any step
    marks the project as 'failed' with a message instead of leaving
    it stuck on 'processing' forever.
    """
    try:
        file_bytes = supabase.storage.from_("uploads").download(storage_path)

        suffix = f".{source_format.lower()}" if source_format else ".dxf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        if source_format in ("DXF", "DWG"):
            # Note: DWG files need conversion to DXF first in production
            # (via the ODA File Converter) — not yet wired here.
            parse_dxf_and_save(tmp_path, project_id)
            build_and_store_report(project_id, user_id)
        elif source_format == "PDF":
            parse_pdf_and_save(tmp_path, project_id, user_id, storage_path)
            build_and_store_report(project_id, user_id)
        elif source_format == "IFC":
            # IFC parsing (via IfcOpenShell) — not yet implemented in
            # this service. Mark for manual follow-up rather than
            # silently doing nothing.
            supabase.table("projects").update({
                "status": "failed",
                "error_message": "IFC extraction isn't wired up yet — DXF, DWG, and PDF are supported.",
            }).eq("id", project_id).execute()
        else:
            supabase.table("projects").update({
                "status": "failed",
                "error_message": f"Unsupported source format: {source_format}",
            }).eq("id", project_id).execute()

        os.unlink(tmp_path)

    except Exception as e:
        supabase.table("projects").update({
            "status": "failed",
            "error_message": str(e)[:500],
        }).eq("id", project_id).execute()


def run_continuation(project_id: str, storage_path: str, source_format: str, user_id: str,
                     first_page: int | None = None, last_page: int | None = None):
    """
    Background task: reads the NEXT window of a PDF drawing set whose earlier
    pages are already extracted (Milestone J16).

    Deliberately NOT wrapped in the "mark the project failed" handler above. A
    project mid-continuation has a truthful status derived from the evidence
    that is persisted against it (Milestone J13 derives it; nothing here writes
    it), and a refused or failed continuation has read nothing and written
    nothing — so it must leave that status alone rather than replace it with a
    failure that describes the ATTEMPT, not the project. A refusal raises with
    the reason it was refused; a caller that wants to know the outcome reads the
    persisted coverage record, which only advances when a window was read in
    full.
    """
    if source_format != "PDF":
        raise ContinuationRefused(
            CONTINUATION_SOURCE_MISMATCH,
            f"only a PDF drawing set is read in windows; this project's source format is "
            f"{source_format!r}",
        )

    file_bytes = supabase.storage.from_("uploads").download(storage_path)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        continue_pdf_extraction(
            tmp_path, project_id, user_id, storage_path,
            requested_first_page=first_page, requested_last_page=last_page,
        )
        build_and_store_report(project_id, user_id)
    finally:
        os.unlink(tmp_path)


def run_retry(project_id: str, storage_path: str, source_format: str, user_id: str, page_number: int):
    """
    Background task: reads ONE page of a PDF drawing set again — the page the
    committed record names as having failed to parse (Milestone J17).

    Deliberately NOT wrapped in the "mark the project failed" handler above, for
    the same reason as `run_continuation`: a project's status is derived from the
    evidence persisted against it (Milestone J13), and a refused retry has read
    nothing and written nothing, so it must leave that status alone rather than
    replace it with a failure that describes the ATTEMPT. A retry whose page still
    cannot be read writes no evidence either — it reports its outcome and leaves
    the page named as failed, exactly as retryable as it was.

    The report is regenerated only when the page was actually read: a retry that
    changed nothing would otherwise overwrite the project's existing report with
    an identical one.
    """
    if source_format != "PDF":
        raise RetryRefused(
            CONTINUATION_SOURCE_MISMATCH,
            f"only a PDF drawing set is read in pages; this project's source format is "
            f"{source_format!r}",
        )

    file_bytes = supabase.storage.from_("uploads").download(storage_path)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = retry_pdf_page(
            tmp_path, project_id, user_id, storage_path, page_number=page_number,
        )
        if result["resolved"]:
            build_and_store_report(project_id, user_id)
    finally:
        os.unlink(tmp_path)


@app.post("/extract/{project_id}")
def extract(project_id: str, background_tasks: BackgroundTasks,
            reviewer=Depends(require_reviewer)):
    project = _authorized_project(project_id, reviewer)
    if not project.get("uploaded_file_path"):
        raise HTTPException(status_code=400, detail="Project has no uploaded file")

    background_tasks.add_task(
        run_extraction,
        project_id,
        project["uploaded_file_path"],
        project.get("source_format"),
        project["user_id"],
    )
    return {"status": "extraction_started", "project_id": project_id}


@app.post("/continue-extraction/{project_id}")
def continue_extraction(project_id: str, background_tasks: BackgroundTasks,
                        first_page: int | None = None, last_page: int | None = None,
                        reviewer=Depends(require_reviewer)):
    """
    Reads the next unread window of this project's PDF drawing set.

    Everything that can be decided from persisted state is decided HERE, while
    the caller is still listening: a project with no window left to read, a
    window that is not the next one, an ambiguous drawing set, a different
    source file or a record that disagrees with itself is answered with the
    reason (409) and queues nothing. Only a genuinely continuable project is
    handed to the background task, which re-decides the same way before it reads
    anything — so a duplicate request cannot read a page twice even if two land
    at once.
    """
    project = _authorized_project(project_id, reviewer)
    if not project.get("uploaded_file_path"):
        raise HTTPException(status_code=400, detail="Project has no uploaded file")
    if (project.get("source_format") or "") != "PDF":
        raise HTTPException(
            status_code=400,
            detail="Only PDF drawing sets are extracted in windows.",
        )

    try:
        plan = plan_continuation(
            project_id, project["uploaded_file_path"],
            requested_first_page=first_page, requested_last_page=last_page,
        )
    except ContinuationRefused as refused:
        raise HTTPException(
            status_code=409,
            detail={"refusal": refused.code, "reason": refused.detail},
        )

    background_tasks.add_task(
        run_continuation,
        project_id,
        project["uploaded_file_path"],
        project.get("source_format"),
        project["user_id"],
        plan.window.first_page,
        plan.window.last_page,
    )
    return {
        "status": "continuation_started",
        "project_id": project_id,
        "first_page": plan.window.first_page,
        "last_page": plan.window.last_page,
    }


@app.post("/retry-extraction/{project_id}")
def retry_extraction(project_id: str, background_tasks: BackgroundTasks, page: int | None = None,
                     reviewer=Depends(require_reviewer)):
    """
    Reads ONE page of this project's PDF drawing set again: the page the committed
    parse-failure record names (Milestone J17).

    Everything that can be decided from persisted state is decided HERE, while the
    caller is still listening — a project or page that does not exist, a page that
    is not recorded as failed, a page past the document, an ambiguous drawing set,
    a different source file, a record that contradicts itself, or a page that
    already has evidence is answered with the reason (409) and queues nothing.
    Only a page that may be read again is handed to the background task, which
    re-decides the same way before it reads anything — so a duplicate request
    cannot read a page twice even if two land at once.

    A `page` that is not a whole number is rejected by the framework's own request
    validation (422) before this handler runs; a `page` that is absent or outside
    the contract reaches it and is refused by name. Two refusals can only be made
    AFTER the read — a read that came back as a different page, and a member mark
    that is already persisted for this project — and those surface as a failed
    background task, not as a synchronous response. See the J17 report.
    """
    project = _authorized_project(project_id, reviewer)
    if not project.get("uploaded_file_path"):
        raise HTTPException(status_code=400, detail="Project has no uploaded file")
    if (project.get("source_format") or "") != "PDF":
        raise HTTPException(
            status_code=400,
            detail="Only PDF drawing sets have pages that can be read again.",
        )

    try:
        plan = plan_retry(project_id, project["uploaded_file_path"], page_number=page)
    except RetryRefused as refused:
        raise HTTPException(
            status_code=409,
            detail={"refusal": refused.code, "reason": refused.detail},
        )

    background_tasks.add_task(
        run_retry,
        project_id,
        project["uploaded_file_path"],
        project.get("source_format"),
        project["user_id"],
        plan.page_number,
    )
    return {
        "status": "retry_started",
        "project_id": project_id,
        "page_number": plan.page_number,
    }


@app.post("/generate-report/{project_id}")
def generate_report(project_id: str, reviewer=Depends(require_reviewer)):
    project = _authorized_project(project_id, reviewer)

    try:
        report_path = build_and_store_report(project_id, project["user_id"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    return {"status": "report_generated", "report_pdf_path": report_path}


# ======================================================================================
# The production review surface (Milestone J19).
#
# The authenticated path to the review surface J18 built. Loading a project is
# READ-ONLY: it reads the project row and its drawing set, states what the record
# already says, and renders it through the SAME contract -> view -> render chain the
# internal review UI uses. It does not extract, does not call a model, does not
# retry, does not write evidence and does not touch the project's status — which is
# why the status it displays is the one J13 already persisted, printed verbatim.
#
# The retry route is the only mutating one. It authenticates and authorizes again on
# its own request, names exactly one project and exactly one page, and then hands
# that page to the existing J17 retry authority — no second retry implementation
# exists anywhere in this file or in the binding above.
# ======================================================================================
def _bound_review(project_id: str, project_row: dict, reviewer):
    """The authorized, bound review record for one project."""
    record = read_project_record(project_id, project_row=project_row)
    if record is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return bind_project_review(
        identity=reviewer,
        record=record,
        decision=authorize_project(reviewer, project_row),
    )


def _production_retry_entry(project: dict, background_tasks: BackgroundTasks):
    """The existing J17 retry, wrapped as the entry the review surface calls.

    It is the same decision and the same handoff the `/retry-extraction` route
    performs: `plan_retry` decides against the persisted record WHILE THE CALLER IS
    STILL LISTENING (so a page that may not be read again is refused by name, with
    nothing queued), and only an accepted page is handed to the existing background
    retry. No page number is validated here — J17 owns that — and the shape it
    returns is the route's own `retry_started`, which the surface already knows how
    to state as "accepted, not yet read".
    """
    def entry(page_number: int):
        plan = plan_retry(
            project["id"], project["uploaded_file_path"], page_number=page_number,
        )
        background_tasks.add_task(
            run_retry,
            project["id"],
            project["uploaded_file_path"],
            project.get("source_format"),
            project["user_id"],
            plan.page_number,
        )
        return {
            "status": "retry_started",
            "project_id": project["id"],
            "page_number": plan.page_number,
        }

    return entry


@app.get("/production/review/{project_id}", response_class=HTMLResponse)
def production_review(project_id: str, reviewer=Depends(require_reviewer)):
    """One authorized project's page-level exceptions, read from its record."""
    project = _authorized_project(project_id, reviewer)
    return render_bound_review(
        _bound_review(project_id, project, reviewer),
        action_prefix=f"/production/review/{project_id}",
    )


@app.post("/production/review/{project_id}/pages/{page_number}/retry", response_class=HTMLResponse)
def production_page_retry(project_id: str, page_number: int, background_tasks: BackgroundTasks,
                          reviewer=Depends(require_reviewer)):
    """Requests the existing J17 retry for exactly the addressed page.

    The project is re-authorized on this request rather than trusted from the
    previous one, and the page is taken from the URL — which J17 then validates
    against the committed record. A page the record does not name as failed, a page
    past the document, a record that disagrees with itself or a project whose
    drawing set is ambiguous is answered by J17's own refusal, stated on the page,
    with nothing read and nothing queued.
    """
    project = _authorized_project(project_id, reviewer)
    if not project.get("uploaded_file_path"):
        raise HTTPException(status_code=400, detail="Project has no uploaded file")
    if (project.get("source_format") or "") != "PDF":
        raise HTTPException(
            status_code=400,
            detail="Only PDF drawing sets have pages that can be read again.",
        )
    binding = _bound_review(project_id, project, reviewer)
    return bound_page_retry(
        binding,
        page_number,
        retry_entry=_production_retry_entry(project, background_tasks),
        action_prefix=f"/production/review/{project_id}",
    )


@app.get("/production/review/{project_id}/workflow", response_class=HTMLResponse)
def production_workflow_review(project_id: str, reviewer=Depends(require_reviewer)):
    """One authorized project's reconstructed review, read from its own record.

    The project is authorized before anything is read, from this request's own
    credential, and the composition reads the persisted review state and rebuilds
    the initial review from the persisted readings. Nothing here writes, and no
    action on the rendered page can be performed by asking for it.
    """
    _authorized_project(project_id, reviewer)
    return render_workflow_review(build_workflow_review(project_id))
