"""
Supabase client singleton para operações server-side.
Usa a service role key (ignora RLS) — nunca expor ao frontend.
"""
import os
from functools import lru_cache
from supabase import create_client, Client


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)
