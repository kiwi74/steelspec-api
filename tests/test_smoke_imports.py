"""
Formalizes the ad-hoc import checks used throughout development into
real, repeatable tests. Every module in the app must import cleanly
with no circular dependencies — this is the cheapest possible test
and it's exactly what caught the pre-restructure call-site bug.
"""
import importlib
import pytest

MODULES = [
    "app.config",
    "app.supabase_client",
    "app.ai_analysis.pdf_vision_analyzer",
    "app.drawing_reading.dxf_parser",
    "app.engineering_data.section_matcher",
    "app.engineering_data.repository",
    "app.validation.rules",
    "app.cad_engine.interface",
    "app.drawing_generator.interface",
    "app.export.storage_export",
    "app.report.pdf_generator",
    "app.pipeline",
    "app.main",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name):
    importlib.import_module(module_name)


def test_api_routes_registered():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/health" in paths
    assert "/extract/{project_id}" in paths
    assert "/generate-report/{project_id}" in paths
