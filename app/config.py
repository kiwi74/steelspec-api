"""
Configuration for the SteelSpec API service.
All values come from environment variables — never hardcode secrets.
"""
import os

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # server-side only, bypasses RLS
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://steelspec.vercel.app").split(",")

# Anthropic API — used for PDF drawing vision analysis. Separate from
# any personal Claude.ai account; needs its own billing at
# console.anthropic.com. Optional at startup so DXF-only deployments
# don't crash — PDF extraction simply fails clearly if unset.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Swappable per Phase 18 of the build spec — don't hard-code the whole
# pipeline around one model. Override via env var to tune cost/accuracy.
PDF_VISION_MODEL = os.environ.get("PDF_VISION_MODEL", "claude-sonnet-4-6")

# Safety cap on pages processed per upload, so a mis-uploaded 300-page
# PDF doesn't silently rack up a huge API bill on one project.
MAX_PDF_PAGES = int(os.environ.get("MAX_PDF_PAGES", "30"))
