# SteelSpec API

The backend service that connects file uploads to real steel extraction.
Downloads a DXF/DWG file from Supabase Storage, parses it, and writes
the extracted steel members straight into the Supabase database.

## What this does right now

- ✅ DXF parsing (layer classification, section matching, member extraction)
- ✅ Writes results into Supabase `steel_members` + updates `projects` status
- ⚠️ DWG files aren't converted to DXF yet (needs the ODA File Converter wired in)
- ❌ IFC parsing isn't implemented yet (needs IfcOpenShell added)
- ❌ Connection extraction (bolts/plates/welds) isn't wired into this service yet — the logic exists from earlier prototyping but needs porting in the same way the member extraction was

## Local development

```
cd steelspec-api
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your real SUPABASE_SERVICE_ROLE_KEY

uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the interactive API docs.

## Getting your service_role key

**This key bypasses Row Level Security — treat it like a master password.**
Never commit it, never put it in frontend code, never share it outside this
service's environment variables.

1. Go to your Supabase project dashboard → Settings → API
2. Under "Project API keys", copy the `service_role` `secret` key
3. Set it as `SUPABASE_SERVICE_ROLE_KEY` wherever you deploy this (see below) — never in the React app's `.env`

## Deploying (Railway — recommended, easiest)

1. Push this `steelspec-api` folder to its own GitHub repo (same pattern as the frontend — `git init`, `git add -A`, `git commit`, push to a new repo like `steelspec-api`)
2. Go to [railway.app](https://railway.app), sign in with GitHub
3. **New Project → Deploy from GitHub repo** → select `steelspec-api`
4. Railway will detect the `Dockerfile` automatically
5. Go to the service's **Variables** tab and add:
   - `SUPABASE_URL` = `https://yzwqhekpardekbczqtce.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` = (from the step above)
   - `ALLOWED_ORIGINS` = `https://steelspec.vercel.app`
6. Railway deploys automatically and gives you a public URL like `https://steelspec-api-production.up.railway.app`

## Connecting it to the frontend

Once deployed, add the Railway URL to the frontend's environment variables in Vercel:

1. Vercel dashboard → your SteelSpec project → Settings → Environment Variables
2. Add `VITE_API_URL` = your Railway URL (no trailing slash)
3. Redeploy the frontend (push any commit, or use Vercel's redeploy)

From that point on, every file upload automatically triggers real extraction.

## What's next (in priority order)

1. **Wire DWG→DXF conversion** using the ODA File Converter, so DWG uploads work end-to-end, not just DXF
2. **Port the connection extraction logic** (bolts, plates, welds) from the earlier prototype into `dxf_parser.py`
3. **Add IFC parsing** using IfcOpenShell — higher accuracy path for engineers using Revit/Tekla
4. **Add PDF report generation** — port the ReportLab-based generator from earlier prototyping, triggered once extraction completes, written to the `reports` Storage bucket
5. **Add a status webhook or polling** so the frontend shows live progress instead of the current simulated progress bar
