"""
Server-side Supabase client using the service_role key.
This bypasses Row Level Security — it's how the backend writes
extracted data on behalf of whichever user uploaded the file.
NEVER expose this key to the frontend.
"""
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
