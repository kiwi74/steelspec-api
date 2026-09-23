"""
Milestone 7AN — the review UI's web entry point.

A small FastAPI application inside the existing SteelSpec architecture that
serves the review pages as plain HTML. The routes do nothing but hand work
to the session layer: rendering pages from the bound workflow's view model,
submitting resolutions through the existing 7AJ workflow boundary, and
refreshing through it. No route computes, decides or validates anything;
no route touches the workflow beyond the session layer.

This slice keeps its own app instance (mounted alongside the main API) so
the review UI can run without the main API's Supabase-dependent imports;
wiring it into app.main is a future integration step, not a new
architecture.
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app.review_ui import render, session

__all__ = ["review_app"]

review_app = FastAPI(title="SteelSpec Review UI")


@review_app.get("/", response_class=HTMLResponse)
def project_page():
    return session.render_project_page()


@review_app.get("/connections/{package_id}", response_class=HTMLResponse)
def connection_page(package_id: str):
    return session.render_connection_page(package_id)


@review_app.post("/connections/{package_id}/resolve", response_class=HTMLResponse)
async def resolve_connection(package_id: str, request: Request):
    form = dict((await request.form()).items())
    return session.submit_resolution(package_id, form)


@review_app.post("/refresh", response_class=HTMLResponse)
def refresh_project():
    return session.refresh()
