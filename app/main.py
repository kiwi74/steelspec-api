"""
SteelSpec API — the bridge between the frontend, Supabase, and the
DXF/IFC parsing engine.

Endpoints:
  POST /extract/{project_id}         — triggers extraction on an uploaded file
  POST /generate-report/{project_id} — (re)generates the PDF report on demand
  GET  /health                       — simple healthcheck
"""
import os
import tempfile
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from app.config import ALLOWED_ORIGINS
from app.supabase_client import supabase
from app.parser.dxf_parser import parse_dxf_and_save
from app.report.pdf_generator import generate_report_pdf

app = FastAPI(title="SteelSpec API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


def build_and_store_report(project_id: str, user_id: str):
    """
    Generates the PDF report for a project and uploads it to the
    private 'reports' bucket, then records the path on the project
    row so the frontend knows a report is ready to unlock.
    """
    pdf_bytes = generate_report_pdf(project_id)
    report_path = f"{user_id}/{project_id}/steel_schedule.pdf"

    # Upload with upsert so re-generating (e.g. after a re-extraction)
    # overwrites the previous file instead of erroring on a duplicate.
    supabase.storage.from_("reports").upload(
        report_path, pdf_bytes,
        file_options={"content-type": "application/pdf", "upsert": "true"},
    )

    supabase.table("projects").update({
        "report_pdf_path": report_path,
    }).eq("id", project_id).execute()

    return report_path


def run_extraction(project_id: str, storage_path: str, source_format: str, user_id: str):
    """
    Background task: download the file from Supabase Storage,
    run the appropriate parser, write results back to the DB, then
    generate the PDF report so it's ready the moment someone pays
    to unlock it. Wrapped in try/except so a failure at any step
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
        elif source_format == "IFC":
            # IFC parsing (via IfcOpenShell) — not yet implemented in
            # this service. Mark for manual follow-up rather than
            # silently doing nothing.
            supabase.table("projects").update({
                "status": "failed",
                "error_message": "IFC extraction isn't wired up yet — DXF/DWG only for now.",
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


@app.post("/extract/{project_id}")
def extract(project_id: str, background_tasks: BackgroundTasks):
    """
    Triggers extraction for a project that's already had its file
    uploaded to Supabase Storage. Runs in the background so the
    HTTP request returns immediately — the frontend polls the
    project's status via Supabase directly.
    """
    result = supabase.table("projects").select("*").eq("id", project_id).single().execute()
    project = result.data
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
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


@app.post("/generate-report/{project_id}")
def generate_report(project_id: str):
    """
    Regenerates the PDF report for a project on demand — useful if
    the extraction data was manually corrected after review, or if
    the report needs refreshing without re-running extraction.
    """
    result = supabase.table("projects").select("*").eq("id", project_id).single().execute()
    project = result.data
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        report_path = build_and_store_report(project_id, project["user_id"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    return {"status": "report_generated", "report_pdf_path": report_path}