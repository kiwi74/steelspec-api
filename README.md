# SteelSpec API

Backend service connecting file uploads to real steel extraction —
DXF, and now PDF drawing sets via Claude vision.

## What this does right now

- ✅ DXF parsing: members + connections (bolts/plates/welds)
- ✅ PDF drawing vision extraction: steel member schedule with
  source page + confidence (first milestone of the PDF analysis
  feature — see "PDF drawing analysis" below)
- ✅ PDF report generation, uploaded to the `reports` bucket
- ⚠️ DWG files aren't converted to DXF yet (needs the ODA File
  Converter wired in)
- ❌ IFC parsing isn't implemented yet (needs IfcOpenShell)
- ❌ PDF connection extraction (bolts/plates/welds from drawings) —
  not yet, see "Deliberately deferred" below

## PDF drawing analysis — first milestone

Scope, deliberately narrow: **PDF → Steel Member Schedule + Source
References**. Upload a PDF drawing set, get back a schedule of every
steel member the model found, with which page it came from and a
confidence score — nothing invented, nothing designed.

### New environment variable required

```
ANTHROPIC_API_KEY=sk-ant-...
```

This is **separate from any personal Claude.ai subscription** — it's
a pay-per-use API key from [console.anthropic.com](https://console.anthropic.com),
billed by usage. Every page of every uploaded PDF sends one image to
Claude's vision API, so cost scales with PDF volume and page count —
factor this into your pricing now that it's a real per-upload cost,
unlike the free DXF parsing.

Optional tuning variables:
```
PDF_VISION_MODEL=claude-sonnet-4-6   # swap models here, e.g. for cost tuning
MAX_PDF_PAGES=30                     # safety cap per upload
```

### Deliberately deferred (see `pdf_vision_parser.py` docstring)

- Connection extraction from PDF drawings (bolts/plates/welds via
  vision) — DXF connection extraction is live; PDF isn't yet.
- Cross-sheet reasoning (an architectural drawing mentioning a mark
  whose size is defined on a different structural sheet).
- A proper drawing register and multi-file "same project" grouping —
  today, one PDF per project, however many pages it contains.
- The split-pane "click a row, see the highlighted source region"
  review UI. Rendered page images ARE stored (in the private
  `drawing-pages` bucket, one row per page in the new
  `drawing_pages` table) — there's just no frontend to view them yet.
- Revision comparison between drawing versions.

These are the logical next slices once the core loop is validated
against a handful of real drawing sets.

## Local development

```
cd steelspec-api
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# poppler-utils must be installed locally too, for pdf2image:
#   macOS: brew install poppler
#   Ubuntu/Debian: apt install poppler-utils

cp .env.example .env
# edit .env with your real SUPABASE_SERVICE_ROLE_KEY and ANTHROPIC_API_KEY

uvicorn app.main:app --reload
```

## Deploying (Railway)

Same as before — push to GitHub, Railway auto-deploys from the
Dockerfile (now installs `poppler-utils` at build time). After
deploying, add the new env vars in Railway's **Variables** tab:

```
ANTHROPIC_API_KEY=<your key from console.anthropic.com>
PDF_VISION_MODEL=claude-sonnet-4-6
MAX_PDF_PAGES=30
```

## What's next (priority order)

1. Test the PDF pipeline against a real structural drawing set from
   Blair — synthetic test files can't validate vision extraction
   quality the way DXF's regex parsing could be validated
2. Wire PDF connection extraction (bolts/plates/welds)
3. Build the source-page viewer UI (images are already stored,
   ready for this)
4. DWG→DXF conversion via ODA File Converter
5. IFC parsing via IfcOpenShell
