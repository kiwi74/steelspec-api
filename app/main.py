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
  POST /production/review/{project_id}/connections/{package_id}/resolve
                                        — records ONE human review of one
                                          connection and delivers what it earns:
                                          a generation, a verified drawing stored
                                          durably and a project pointed at it, for
                                          an AUTO outcome; a recorded revision for
                                          every outcome (Milestone J47)
  POST /production/review/{project_id}/open
                                        — opens the project's review by recording
                                          its reconstructed revision-0 baseline.
                                          Generates nothing, stores nothing and
                                          resolves nothing (Milestone J50)
  POST /production/review/{project_id}/document-role
                                        — records the role a human ASSERTS for one
                                          of the project's own source documents.
                                          Infers no role, selects no source and
                                          changes no evidence (Milestone J64)
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
import logging
import os
import tempfile
from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import ALLOWED_ORIGINS, SUPABASE_URL
from app.storage_download import download_from_uploads
from app.supabase_client import supabase
from app.drawing_reading.dxf_parser import parse_dxf_and_save
from app.pipeline import (
    continue_pdf_extraction,
    parse_pdf_and_save,
    plan_continuation,
    plan_first_window,
    plan_retry,
    retry_pdf_page,
)
from app.validation.page_windows import CONTINUATION_SOURCE_MISMATCH, ContinuationRefused
from app.validation.parse_failures import RetryRefused
from app.production_extraction_source import (
    ExtractionInputRefused,
    parse_extraction_source_request,
    resolve_extraction_source,
)
from app.report.pdf_generator import generate_report_pdf
from app.export.storage_export import upload_and_record
from app.production_annotation_evidence import (
    build_annotation_evidence_review,
    render_annotation_evidence_review,
)
from app.production_connection_review import build_workflow_review, render_workflow_review
from app.production_review_wire import workflow_wire
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
from app.cad_engine.project_workflow import (
    ConnectionAlreadyProcessedError,
    CrossProjectResolutionError,
    StaleProjectWorkflowError,
    UnknownProjectPackageError,
    WorkflowStageFailureError,
)
from app.production_fabrication_artifact import ArtifactRefused
from app.production_review.project_review_claim import ClaimRefused
from app.production_review.project_workflow_resumption import (
    ResolutionTaskRefused,
    ResumptionRefused,
)
from app.production_review_resolution import (
    RESOLUTION_REFUSED_CONCURRENT_REVISION,
    RESOLUTION_REFUSED_NO_REVIEW_LAYER,
    RESOLUTION_REFUSED_NO_SUCH_CONNECTION,
    RESOLUTION_REFUSED_PROJECT_UNKNOWN,
    ResolutionInputRefused,
    ResolutionRouteRefused,
    parse_resolution_request,
    resolve_production_connection,
)
from app.production_review_opening import (
    OPENING_REFUSED_PROJECT_UNKNOWN,
    OpeningInputRefused,
    OpeningRefused,
    open_project_review,
    parse_opening_request,
)
from app.production_document_role import (
    DocumentRoleInputRefused,
    DocumentRoleRefused,
    assert_document_role,
    parse_document_role_request,
)
from app.review_ui.web import review_app

# Wave 3J Phase B — the persisted failure classification. `safe_failure_message` is the
# only value this module may put into `projects.error_message`; it returns one of a
# frozen set of SteelSpec-owned strings and can carry no exception text, response body
# or URL, whatever it is handed.
from app.validation.failure_classification import (
    ReportGenerationFailed,
    safe_failure_message,
)

# Wave 3H Phase A — the module logger for this process's operator diagnostics.
#
# Deliberately unconfigured. The standard library's own last-resort handling routes a
# record at WARNING or above to stderr when no handler is attached, and uvicorn's default
# logging configuration configures only its own `uvicorn*` loggers — it leaves the root
# logger alone and sets `disable_existing_loggers: False`. So a `logger.exception(...)`
# here reaches the process's stderr with no configuration, no dependency and no change to
# how the service is deployed. Railway captures deployment stderr and normalizes it to
# `level.error`; nothing in this application serves that stream over any route.
logger = logging.getLogger(__name__)

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
    """Generate the project's report and store it. Returns its storage path.

    Wave 3J Phase B — the body is wrapped so that a report failure arrives at the
    caller as `ReportGenerationFailed` rather than as whatever raised it. Nothing
    about the LIFECYCLE changed: the same exceptions are caught, the same work is
    attempted in the same order, and a failure still propagates to exactly the
    handlers that caught it before — `run_extraction`'s broad handler still marks the
    project failed, and the retry and continuation paths still leave status alone.

    What changed is that the failure is now NAMEABLE at the boundary that persists it,
    which is what lets `projects.error_message` say "the report could not be generated"
    without reading the exception's text. The originating exception is preserved as the
    cause, so the Phase A traceback an operator reads is unchanged and complete.
    """
    try:
        pdf_bytes = generate_report_pdf(project_id)
        report_path = f"{user_id}/{project_id}/steel_schedule.pdf"
        upload_and_record(
            bucket="reports", path=report_path, content=pdf_bytes, content_type="application/pdf",
            table="projects", record_id=project_id, path_column="report_pdf_path",
        )
        return report_path
    except Exception as exc:
        # A fixed SteelSpec-owned sentence, interpolating nothing. `from exc` keeps the
        # detail on the operator channel; the raised object carries none of it.
        raise ReportGenerationFailed(
            "SteelSpec could not generate or store the project report."
        ) from exc


def _safe_source_format_label(source_format) -> str:
    """The source format as it may appear inside a persisted message.

    Wave 3J Phase B. This value is read from the project row and arrives here as
    whatever was stored — not exception text, but still a free-form value being
    interpolated into a column a reader sees. It is admitted only if it is a short
    ASCII alphanumeric token, and replaced by a fixed word otherwise, so that no
    stored value can reshape the sentence it appears in. A normal format ("STEP",
    "IFC") is passed through unchanged, so the existing message is unchanged for
    every project whose row holds a real format.
    """
    if (isinstance(source_format, str)
            and 0 < len(source_format) <= 16
            and source_format.isascii()
            and source_format.isalnum()):
        return source_format
    return "unrecognised"


def run_extraction(project_id: str, storage_path: str, source_format: str, user_id: str):
    """
    Background task: download the file from Supabase Storage, run the
    appropriate parser, write results back to the DB, then generate
    the PDF report. Wrapped in try/except so a failure at any step
    marks the project as 'failed' with a message instead of leaving
    it stuck on 'processing' forever.
    """
    try:
        # Bounded read (Milestone J37C-8Q): a 120 s per-read deadline and a 900 s
        # total deadline in place of storage3's 20 s read default — see
        # app/storage_download.py for why the transfer, not the stall, was the
        # wrong thing to bound.
        file_bytes = download_from_uploads(storage_path, client=supabase)

        # Wave 3J Phase B — the stored format is normalized ONCE, before either of its
        # two uses: the temp-file suffix immediately below, and the persisted sentence at
        # the end of this function. It is the project row's value rather than exception
        # text, but it is still an external string, and it was previously reaching BOTH a
        # filesystem path (where a separator in it made `NamedTemporaryFile` raise) and a
        # sentence a reader sees. It is now admitted only as a short ASCII alphanumeric
        # token; anything else becomes a fixed word rather than being reproduced.
        format_label = _safe_source_format_label(source_format)

        suffix = f".{format_label.lower()}" if source_format else ".dxf"
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
            # Wave 3J Phase B — the format is normalized before it is interpolated.
            # The message is still SteelSpec's own sentence naming a format the row
            # stated; nothing the row could hold can put anything else into it.
            supabase.table("projects").update({
                "status": "failed",
                "error_message": f"Unsupported source format: {format_label}",
            }).eq("id", project_id).execute()

        os.unlink(tmp_path)

    except Exception as e:
        # Wave 3H Phase A — the operator diagnostic destination for this boundary.
        #
        # Before this milestone the row written below was the ONLY record that a
        # background extraction had failed, and it is also the only record an ordinary
        # reader of the project can see. Those are two different audiences with two
        # different needs, and one column was serving both. This log separates them: the
        # traceback — the part an operator needs and no reader should have — goes to the
        # process stream, and the row is left exactly as it was.
        #
        # The message interpolates nothing. No exception text, no provider response body,
        # no request or response body, no URL and no credential appears in it; the
        # exception's own text travels only inside the traceback that `exc_info` attaches.
        #
        # Logged BEFORE the write, so that a project row which cannot be updated still
        # leaves a diagnostic behind.
        #
        # Wave 3J Phase B — the persisted value is now a SteelSpec-owned classification
        # rather than `str(e)[:500]`. That column is read by the project's own page and
        # by the dashboard, so before this milestone a provider response body, a
        # PostgREST envelope (whose `details` can carry real row values) or a storage URL
        # could be displayed to whoever opened the project. The classification says what
        # happened without carrying any of it; the exception's own text still reaches the
        # operator, in the traceback `exc_info` attaches to the record above.
        #
        # The status, the write ordering and the single project row written are all
        # unchanged: only the value changed.
        logger.exception("SteelSpec extraction task failed")
        supabase.table("projects").update({
            "status": "failed",
            "error_message": safe_failure_message(e),
        }).eq("id", project_id).execute()


def run_continuation(project_id: str, storage_path: str, source_format: str, user_id: str,
                     first_page: int | None = None, last_page: int | None = None,
                     document_id: str | None = None):
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

    file_bytes = download_from_uploads(storage_path, client=supabase)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        continue_pdf_extraction(
            tmp_path, project_id, user_id, storage_path,
            document_id=document_id,
            requested_first_page=first_page, requested_last_page=last_page,
        )
        build_and_store_report(project_id, user_id)
    finally:
        os.unlink(tmp_path)


def run_retry(project_id: str, storage_path: str, source_format: str, user_id: str, page_number: int,
              document_id: str | None = None):
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

    file_bytes = download_from_uploads(storage_path, client=supabase)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = retry_pdf_page(
            tmp_path, project_id, user_id, storage_path,
            page_number=page_number, document_id=document_id,
        )
        if result["resolved"]:
            build_and_store_report(project_id, user_id)
    finally:
        os.unlink(tmp_path)


def _extraction_source(project: dict, body, *, status_code: int = 409):
    """The source this extraction request reads, or the refusal that answers it.

    Milestone J63. One statement of the contract the three extraction routes share: the
    body may carry exactly the optional `document_id`, and the source is then resolved
    against the project's own persisted documents
    (`app/production_extraction_source.py`, which owns every rule and every refusal code
    here). No route decides any part of this for itself.

    A malformed body is 422 — the caller's own mistake, answered before any persisted state
    is read — and a source that cannot be resolved is `status_code`: a request that names a
    document this project does not have is a conflict with persisted state (409), and
    nothing has been downloaded, queued or written when this raises.
    """
    try:
        document_id = parse_extraction_source_request(body)
    except ExtractionInputRefused as refused:
        raise HTTPException(
            status_code=422,
            detail=_route_refusal_detail(refused.code, refused.statement),
        )
    try:
        return resolve_extraction_source(
            project["id"], document_id=document_id, project=project,
        )
    except ContinuationRefused as refused:
        raise HTTPException(
            status_code=status_code,
            detail=_route_refusal_detail(refused.code, refused.detail),
        )


@app.post("/extract/{project_id}")
def extract(project_id: str, background_tasks: BackgroundTasks,
            body: object = Body(default=None),
            reviewer=Depends(require_reviewer)):
    """Reads one source document of this project (Milestones J63, J63A).

    The source is the project's own uploaded file unless the caller ADDRESSES one of the
    project's `project_documents` rows by id, in which case that document's own storage
    path is read instead — the project-level column stops being the only possible source.
    Everything else about the run is unchanged, including the identity it records: J61's
    content rule decides which document the bytes that were read belong to, and a
    re-extraction of a document whose identity is proven resolves back to that same row
    rather than creating a second document.

    The body may carry exactly the optional `document_id` (422 otherwise, by name), the
    project is re-authorized from this request's own credential, and a document that is
    not one of this project's is refused (409) before anything is downloaded or queued.

    An ADDRESSED document is the source, and an OMITTED one reads the project-level
    column — but only on a project whose source is not ambiguous. This route starts a NEW
    reading, so on a project that holds more than one document an omitted address is not
    a default, it is a choice, and the choice is refused rather than made
    (`plan_first_window`, 409, J16's own `CONTINUATION_DRAWING_SET_UNRESOLVED`). A
    project's FIRST extraction is unaffected: with no document, or with one, the request
    is answered exactly as it was before J63 existed.
    """
    project = _authorized_project(project_id, reviewer)
    source = _extraction_source(project, body, status_code=409)
    try:
        source = plan_first_window(project_id, source)
    except ContinuationRefused as refused:
        raise HTTPException(
            status_code=409,
            detail=_route_refusal_detail(refused.code, refused.detail),
        )
    if not source.storage_path:
        raise HTTPException(status_code=400, detail="Project has no uploaded file")

    background_tasks.add_task(
        run_extraction,
        project_id,
        source.storage_path,
        project.get("source_format"),
        project["user_id"],
    )
    return {"status": "extraction_started", "project_id": project_id}


@app.post("/continue-extraction/{project_id}")
def continue_extraction(project_id: str, background_tasks: BackgroundTasks,
                        first_page: int | None = None, last_page: int | None = None,
                        body: object = Body(default=None),
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

    The body may carry exactly the optional `document_id` (Milestone J63), which
    names WHICH of the project's source documents these pages belong to. Omitted,
    the source is the project-level column and every rule above is unchanged.
    The path the background task downloads from is the one the plan proved — the
    resolved document's own stored path — so the task re-decides from the same
    source the caller was answered about.
    """
    project = _authorized_project(project_id, reviewer)
    source = _extraction_source(project, body)
    if not source.storage_path:
        raise HTTPException(status_code=400, detail="Project has no uploaded file")
    if (project.get("source_format") or "") != "PDF":
        raise HTTPException(
            status_code=400,
            detail="Only PDF drawing sets are extracted in windows.",
        )

    try:
        plan = plan_continuation(
            project_id, source.storage_path, document_id=source.document_id,
            requested_first_page=first_page, requested_last_page=last_page,
        )
    except ContinuationRefused as refused:
        raise HTTPException(
            status_code=409,
            detail={"refusal": refused.code, "reason": refused.detail},
        )

    # The window is dispatched positionally, exactly as it always was; the document the
    # caller addressed (when it addressed one) rides beside it by name, so a task that
    # re-decides re-decides about the SAME document.
    background_tasks.add_task(
        run_continuation,
        project_id,
        plan.drawing["storage_path"],
        project.get("source_format"),
        project["user_id"],
        plan.window.first_page,
        plan.window.last_page,
        document_id=source.document_id,
    )
    return {
        "status": "continuation_started",
        "project_id": project_id,
        "first_page": plan.window.first_page,
        "last_page": plan.window.last_page,
    }


@app.post("/retry-extraction/{project_id}")
def retry_extraction(project_id: str, background_tasks: BackgroundTasks, page: int | None = None,
                     body: object = Body(default=None),
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

    The body may carry exactly the optional `document_id` (Milestone J63), which
    names WHICH of the project's source documents this page belongs to: a page's
    failure is a fact about one document's reading, so the record it is checked
    against must be that document's. Omitted, the source is the project-level
    column and every rule above is unchanged.
    """
    project = _authorized_project(project_id, reviewer)
    source = _extraction_source(project, body)
    if not source.storage_path:
        raise HTTPException(status_code=400, detail="Project has no uploaded file")
    if (project.get("source_format") or "") != "PDF":
        raise HTTPException(
            status_code=400,
            detail="Only PDF drawing sets have pages that can be read again.",
        )

    try:
        plan = plan_retry(
            project_id, source.storage_path,
            page_number=page, document_id=source.document_id,
        )
    except RetryRefused as refused:
        raise HTTPException(
            status_code=409,
            detail={"refusal": refused.code, "reason": refused.detail},
        )

    # The page is dispatched positionally, exactly as it always was; the document the
    # caller addressed (when it addressed one) rides beside it by name.
    background_tasks.add_task(
        run_retry,
        project_id,
        plan.drawing["storage_path"],
        project.get("source_format"),
        project["user_id"],
        plan.page_number,
        document_id=source.document_id,
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
    except Exception as exc:
        # Wave 3J Phase B — this response body is read by a client, so it carries the
        # same frozen SteelSpec classification the project row does, and never the
        # exception's text. Removing the interpolation without replacing the destination
        # would have destroyed the diagnostic rather than redirected it, so the detail
        # goes where Phase A sends every other extraction failure: the operator stream,
        # with its traceback.
        logger.exception("SteelSpec report generation failed")
        raise HTTPException(status_code=500, detail=safe_failure_message(exc))

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


# ======================================================================================
# E2E-001A — the production review READ contract.
#
# The JSON twin of the route above: the SAME composition, the same authorization, the
# same refusal vocabulary — as data instead of a page. It is read-only in the strict
# sense (no claim, no revision, no artifact, no working directory, no write of any
# kind) and it names nothing below the route: the rendering lives in
# `app.production_review_wire`, exactly as the page's rendering lives in
# `app.production_connection_review`.
# ======================================================================================
@app.get("/production/review/{project_id}/review")
def production_review_read(project_id: str, reviewer=Depends(require_reviewer)):
    """One authorized project's review as structured JSON (E2E-001A).

    Authorized before anything is read, from this request's own credential, exactly as
    the page route above is. The composition is that route's own — this function adds no
    rule, re-derives no status and writes nothing at all.
    """
    _authorized_project(project_id, reviewer)
    return workflow_wire(build_workflow_review(project_id))


@app.get("/production/review/{project_id}/annotations", response_class=HTMLResponse)
def production_annotation_evidence(project_id: str,
                                   page: int | None = Query(default=None, ge=1),
                                   reviewer=Depends(require_reviewer)):
    """One authorized project's recorded PDF annotation evidence, as J28 read it.

    The project is authorized before anything is read, from this request's own
    credential, and the optional page selector only narrows which pages are SHOWN — it
    never narrows what is read. This surface is a viewer: it renders what the evidence
    layer recorded, creates nothing, resolves nothing and changes nothing. Where the
    evidence store is not present in the deployment, that is rendered as its own state
    rather than acted on.
    """
    _authorized_project(project_id, reviewer)
    return render_annotation_evidence_review(
        build_annotation_evidence_review(project_id, page=page)
    )


# ======================================================================================
# Milestone J47 — the production review resolution route.
#
# The FIRST route in this application that performs a real production write: it records
# a human review, may generate and durably store a fabrication drawing, and may point a
# project at it. Everything it composes already existed — J44's claim, J46's resumption,
# 7AJ's resolver (which itself runs 7AC/7AD/7AA/7Z/7AE/7AF/7AG), J45's storage, J22's
# persistence — and this route invents no gate, no status and no second authority.
#
# WHAT THE CLIENT MAY NOT SAY. The body is `{"expected_revision": int, "resolutions":
# [...]}` and nothing else. The project comes from the URL and is authorized against this
# request's own token; the connection, the reviewer, the claim, the artifact's location
# and every decision/status come from server-controlled state. A body naming any of them
# is REFUSED rather than quietly corrected, which is why the parser is the first thing
# that runs and why its refusals are 422 rather than ignored.
# ======================================================================================
def _route_refusal_detail(code: str, statement: str, **extra) -> dict:
    """One refusal, in the shape the other production routes already use."""
    detail = {"refusal": code, "reason": statement}
    detail.update(extra)
    return detail


def _status_for_route_refusal(refused: ResolutionRouteRefused) -> int:
    """The status a stated route refusal is answered with.

    A refusal whose cause is the ADDRESSED THING NOT EXISTING is 404; one whose cause is
    the project's CURRENT STATE is 409; one whose cause is this deployment or the store
    failing is 500. The mapping is by the refusal's own code, so a caller can branch on
    the code and the status never disagrees with it.
    """
    if refused.code in (
        RESOLUTION_REFUSED_PROJECT_UNKNOWN,
        RESOLUTION_REFUSED_NO_SUCH_CONNECTION,
    ):
        return 404
    if refused.code in (
        RESOLUTION_REFUSED_NO_REVIEW_LAYER,
        RESOLUTION_REFUSED_CONCURRENT_REVISION,
    ):
        return 409
    return 500


@app.post("/production/review/{project_id}/connections/{package_id}/resolve")
def production_connection_resolve(
    project_id: str,
    package_id: str,
    body: dict = Body(...),
    reviewer=Depends(require_reviewer),
):
    """Records one human review of one connection, and delivers what it earns.

    The order is the safety property, and it is J47's own: authenticate, authorize, read
    the request, validate the artifact working directory, take the project claim, resume
    the workflow, validate the tasks belong to this connection, resolve, then — for an
    AUTO outcome whose artifact genuinely verified — store the artifact durably, record
    the revision, point the project at it, and release the claim.

    A generation, verification, upload or pointer failure is not an HTTP error: the
    review happened, the revision is recorded, and what did not happen is named in
    `failures`. What IS an HTTP error is a request that never became a review — a caller
    who is not identified, does not own the project, named something that is not there,
    held a stale revision, or lost the claim to another reviewer.

    `durable_artifact_path` is the Storage identity of an artifact that was stored, and
    is stated even when a later step failed, so a caller learns of a stored-but-unpointed
    artifact rather than assuming nothing was written. No local path is ever returned.
    """
    # AUTHORIZATION before anything is read, from this request's own credential.
    project = _authorized_project(project_id, reviewer)
    binding = _bound_review(project_id, project, reviewer)

    # The request body, before any side effect. A malformed request is malformed
    # whatever this deployment's configuration is, so it is answered as itself rather
    # than as whichever deployment problem would have been found first.
    try:
        request = parse_resolution_request(body)
    except ResolutionInputRefused as refused:
        raise HTTPException(
            status_code=422,
            detail=_route_refusal_detail(refused.code, refused.statement),
        )

    try:
        outcome = resolve_production_connection(
            binding=binding,
            package_id=package_id,
            request=request,
        )
    except ClaimRefused as refused:
        # J44's refusal already carries when the lease ends, so a caller learns when the
        # project becomes reviewable again without learning WHO holds it. The holder is
        # never named here, and nothing in this route could name it: no claim holder is
        # read, held or logged.
        raise HTTPException(
            status_code=409,
            detail=_route_refusal_detail(
                refused.code, refused.statement, lease_expires_at=refused.lease_expires_at,
            ),
        )
    except ResolutionTaskRefused as refused:
        raise HTTPException(
            status_code=422,
            detail=_route_refusal_detail(refused.code, refused.detail),
        )
    except ResumptionRefused as refused:
        raise HTTPException(
            status_code=409,
            detail=_route_refusal_detail(refused.code, refused.detail),
        )
    except ResolutionRouteRefused as refused:
        raise HTTPException(
            status_code=_status_for_route_refusal(refused),
            detail=_route_refusal_detail(
                refused.code,
                refused.statement,
                durable_artifact_path=refused.durable_artifact_path,
            ),
        )
    except UnknownProjectPackageError as refused:
        raise HTTPException(
            status_code=404,
            detail=_route_refusal_detail("UNKNOWN_PROJECT_PACKAGE", str(refused)),
        )
    except (
        StaleProjectWorkflowError,
        ConnectionAlreadyProcessedError,
        CrossProjectResolutionError,
    ) as refused:
        raise HTTPException(
            status_code=409,
            detail=_route_refusal_detail(type(refused).__name__, str(refused)),
        )
    except WorkflowStageFailureError as refused:
        raise HTTPException(
            status_code=500,
            detail=_route_refusal_detail(type(refused).__name__, str(refused)),
        )
    except ArtifactRefused as refused:
        # J45 refused before storing anything OR refused the working directory. Both are
        # this deployment's condition rather than this caller's, and both are answered
        # without pretending any artifact exists.
        raise HTTPException(
            status_code=500,
            detail=_route_refusal_detail(refused.code, refused.statement),
        )
    except ValueError as refused:
        # The existing exception-resolution contract refused an answer against its task —
        # an answer type it does not carry, a task it does not hold, an answer already
        # decided. It raises plain ValueError rather than a named type, and everything
        # that could mean is the CALLER's to fix, so it is 422. Every refusal type above
        # is itself a ValueError, which is why this clause is last and why that ordering
        # is load-bearing rather than stylistic.
        raise HTTPException(
            status_code=422,
            detail=_route_refusal_detail("RESOLUTION_REFUSED_BY_THE_CONTRACT", str(refused)),
        )

    return outcome.to_response()


# ======================================================================================
# Milestone J50 — the production review baseline-opening route.
#
# The SECOND route in this application that performs a production write, and the smallest
# one it could be: it records the reconstruction's own revision-0 baseline and nothing
# else. J48 established that a project's first recorded revision is 0 and that J22's
# writer accepts it; J49 established that no production caller recorded it, and that
# without it a review cannot start at all — J22's builder derives `expected = 0` from an
# empty chain while the first human resolution is already revision 1, so the baseline can
# never be a side effect of a resolution. This route is the explicit operation J49
# designed.
#
# NO CLAIM AND NO WORKING DIRECTORY. J44's claim is a liveness device, not the correctness
# barrier — that is J22's advisory-locked append-only write — and an open generates no
# artifact, so `require_artifact_working_directory` is not called and `ARTIFACT_WORKING_DIR`
# need not be set for a review to be opened.
#
# WHAT THE CLIENT MAY NOT SAY. Nothing. The body is empty; a body naming a field at all is
# refused. The project comes from the URL, the reviewer from the access token, the revision
# from the reconstruction and the evidence from the project's own persisted rows.
# ======================================================================================
def _status_for_opening_refusal(refused: OpeningRefused) -> int:
    """The status an opening refusal is answered with.

    A refusal whose cause is the ADDRESSED THING NOT EXISTING is 404; one whose cause is
    this deployment or the store failing is 500. The mapping is by the refusal's own code,
    so a caller can branch on the code and the status never disagrees with it.
    """
    if refused.code == OPENING_REFUSED_PROJECT_UNKNOWN:
        return 404
    return 500


@app.post("/production/review/{project_id}/open")
def production_review_open(
    project_id: str,
    body: object = Body(default=None),
    reviewer=Depends(require_reviewer),
):
    """Opens one authorized project's review by recording its revision-0 baseline.

    The order is small and it is the whole of the operation: authenticate, authorize,
    read the request, then — only when the project has recorded NOTHING — reconstruct the
    workflow, record its revision-0 baseline through J22 and read that baseline back.

    It is idempotent: a project whose review is already open is reported as it is, with no
    reconstruction, no write and no revision consumed. It generates nothing, verifies
    nothing, uploads nothing, points at nothing, resolves nothing and dispatches nothing,
    and it takes no claim and needs no artifact working directory.

    A caller who is not identified (401), does not own the project (403), names a project
    that is not there (404), or sends a body carrying any field other than the optional
    `document_id` (422), never reaches the store. A baseline that J22 refuses is not
    reported as an open review.

    `document_id` (Milestone J61) is OPTIONAL and says which of the project's source
    documents this review is about. Omitting it is the ordinary request and resolves exactly
    as it did before this field existed — a project with one readable document is opened
    without naming it. It is never inferred from the URL, the token, the drawing set or the
    project's other rows: a project with several readable documents and no `document_id`
    named is refused by the reconstruction rather than guessed at.
    """
    # AUTHORIZATION before anything is read, from this request's own credential.
    project = _authorized_project(project_id, reviewer)
    binding = _bound_review(project_id, project, reviewer)

    # The request body, before any state is read or written. The only thing an opening
    # request may carry is the document it is about; everything else is the caller's
    # mistake to be told about rather than one to interpret.
    try:
        document_id = parse_opening_request(body)
    except OpeningInputRefused as refused:
        raise HTTPException(
            status_code=422,
            detail=_route_refusal_detail(refused.code, refused.statement),
        )

    try:
        opening = open_project_review(binding=binding, document_id=document_id)
    except OpeningRefused as refused:
        raise HTTPException(
            status_code=_status_for_opening_refusal(refused),
            detail=_route_refusal_detail(
                refused.code, refused.statement, baseline_recorded=refused.baseline_recorded,
            ),
        )
    except ResumptionRefused as refused:
        # J46's own refusal, propagating unchanged: the persisted chain cannot be read, or
        # the reconstruction is not at the revision an open requires. It is the project's
        # state rather than this request's shape, so it is a conflict.
        raise HTTPException(
            status_code=409,
            detail=_route_refusal_detail(refused.code, refused.detail),
        )

    return opening.to_response()


@app.post("/production/review/{project_id}/document-role")
def production_document_role(
    project_id: str,
    body: object = Body(default=None),
    reviewer=Depends(require_reviewer),
):
    """Records the role a human ASSERTS for one of this project's own documents.

    The order is the whole of the operation: authenticate, authorize, read the request,
    then — only for a document THIS project keeps — write the asserted role and report what
    the store says it now holds.

    The role is an assertion and never an inference. Nothing here reads a file, a filename,
    an extension, a page count, a path, a drawing number or any model output, and nothing
    this route writes is later used to choose a source: extraction, continuation, retry,
    reconstruction and opening are unchanged by a role, and J63A's ambiguity rule still
    refuses an unaddressed extraction on a multi-document project whatever roles its
    documents carry.

    A caller who is not identified (401), does not own the project (403), names a project
    that is not there (404), sends a body that names no document or no role, names a field
    this boundary never takes, or asserts a role outside the stored vocabulary (422), or
    addresses a document that is not one of this project's own (409), reaches no write.
    Re-asserting the role a document already holds writes nothing at all.

    The vocabulary is the one J61's schema already stores and enforces — no column, no value
    and no migration is added by this route, and a role the database would refuse to store
    is refused here first.
    """
    # AUTHORIZATION before anything is read, from this request's own credential.
    _authorized_project(project_id, reviewer)

    try:
        request = parse_document_role_request(body)
    except DocumentRoleInputRefused as refused:
        raise HTTPException(
            status_code=422,
            detail=_route_refusal_detail(refused.code, refused.statement),
        )

    try:
        assertion = assert_document_role(
            project_id, document_id=request.document_id, role=request.role,
        )
    except DocumentRoleInputRefused as refused:
        # The vocabulary rule, stated at the second boundary as well. Unreachable through
        # this route — the parser above refused the same value first — and kept because the
        # rule has to hold for every caller of the operation, not only for this one.
        raise HTTPException(
            status_code=422,
            detail=_route_refusal_detail(refused.code, refused.statement),
        )
    except DocumentRoleRefused as refused:
        raise HTTPException(
            status_code=409,
            detail=_route_refusal_detail(refused.code, refused.statement),
        )

    return assertion.to_response()
