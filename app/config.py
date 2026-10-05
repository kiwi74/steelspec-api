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

# Where the fabrication artifact layer builds an artifact before it is verified
# and uploaded. Deliberately has NO default: a fabricated drawing is a
# deliverable, and writing it into whatever directory the process happened to
# start in — or into a shared /tmp — is how one project's drawing ends up beside
# another's. Unset means the artifact layer refuses, at use time, rather than
# guessing a location. Read with .get, not [...], for the same reason
# ANTHROPIC_API_KEY is: this module is imported by much of the service, and a
# key that is only needed on the fabrication path must not break every other
# import. `app.production_fabrication_artifact` validates it (absolute,
# creatable, writable) before use.
ARTIFACT_WORKING_DIR = os.environ.get("ARTIFACT_WORKING_DIR")
