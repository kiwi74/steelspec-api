"""
Generic export/storage helper. Currently used by the report generator
to upload a finished PDF and record its path — kept here rather than
inline in main.py so the future drawing_generator (DXF/STEP export)
can reuse the same upload-and-record pattern without duplicating it.
"""
from app.supabase_client import supabase


def upload_and_record(bucket: str, path: str, content: bytes, content_type: str,
                       table: str, record_id: str, path_column: str) -> str:
    """
    Uploads bytes to a Storage bucket, then records the resulting path
    on the given table/row. Returns the storage path.
    """
    supabase.storage.from_(bucket).upload(
        path, content, file_options={"content-type": content_type, "upsert": "true"},
    )
    supabase.table(table).update({path_column: path}).eq("id", record_id).execute()
    return path
