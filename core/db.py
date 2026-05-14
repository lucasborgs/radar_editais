"""
Supabase client factories.

Há dois modos de uso:

1) Service-role (bypassa RLS) — use APENAS em pipelines, jobs e scripts CLI.
   Nunca utilize em handlers que processam requisições autenticadas de usuários.

2) Per-request com JWT do usuário (anon key + Authorization: Bearer <jwt>) —
   é o modo a ser usado em qualquer rota FastAPI que serve usuário final.
   Assim, todas as queries passam pelas políticas RLS do Postgres, que se
   torna a camada real de defesa de multi-tenancy.

Ver ADR-001 (decisão B5 + addendum M7).
"""
import os
from functools import lru_cache

from supabase import Client, create_client


@lru_cache(maxsize=1)
def get_supabase_service() -> Client:
    """Cliente Supabase com a service role key. Bypassa RLS.

    Cacheado como singleton. Use somente em pipelines/jobs/scripts.
    """
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def get_supabase_user(jwt: str) -> Client:
    """Cliente Supabase com a anon key e o JWT do usuário injetado.

    NÃO é cacheado — cada request precisa de seu próprio token.
    Todas as queries efetuadas com esse cliente respeitam as políticas RLS
    avaliadas no Postgres em função de auth.uid().
    """
    url = os.environ["SUPABASE_URL"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    client = create_client(url, anon_key)

    # Caminho principal: API pública do supabase-py >=2.4
    # Propaga o token para o cliente PostgREST embutido (define o header
    # Authorization: Bearer <jwt> em todas as queries via .table()).
    try:
        client.postgrest.auth(jwt)
    except Exception:
        # Fallback defensivo: caso a versão do SDK não exponha postgrest.auth,
        # injeta o header manualmente. Cobre tanto PostgREST quanto Storage.
        bearer = f"Bearer {jwt}"
        try:
            client.postgrest.session.headers["Authorization"] = bearer
        except Exception:
            pass
        try:
            client.options.headers["Authorization"] = bearer  # type: ignore[attr-defined]
        except Exception:
            pass

    return client


# -----------------------------------------------------------------------------
# Compatibilidade retroativa
# -----------------------------------------------------------------------------
# DEPRECATED: prefira `get_supabase_service()` em código novo.
# Mantido para que pipelines/jobs/scripts antigos continuem funcionando sem
# alterações. Rotas FastAPI NÃO devem usar este alias — use a dependência
# `DbClient`/`get_db` em core/auth.py para obter um cliente per-request
# autenticado com o JWT do usuário.
def get_supabase() -> Client:
    return get_supabase_service()
