"""
Configuration for the SteelSpec API service.
All values come from environment variables — never hardcode secrets.
"""
import os

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # server-side only, bypasses RLS
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://steelspec.vercel.app").split(",")
