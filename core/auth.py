"""
Auth utilities — verificação de JWT emitido pelo Supabase Auth.

Fluxo:
  1. Frontend usa @supabase/supabase-js → signInWithOtp(email) para magic link
  2. Supabase envia o email, usuário clica e recebe JWT
  3. Frontend envia JWT em Authorization: Bearer <token>
  4. Backend verifica com SUPABASE_JWT_SECRET
  5. Backend cria um Supabase client per-request com a anon key + esse JWT,
     de forma que todas as queries passem pelas políticas RLS do Postgres.
"""
import os
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.db import get_supabase_user
from supabase import Client

_security = HTTPBearer()

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
SUPABASE_JWT_AUDIENCE = "authenticated"


def _decode_token(token: str) -> dict:
    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET não configurada",
        )
    try:
        return jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience=SUPABASE_JWT_AUDIENCE,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido") from None


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_security)],
) -> dict:
    """FastAPI dependency — retorna payload do JWT do usuário autenticado."""
    return _decode_token(credentials.credentials)


def get_user_id(payload: Annotated[dict, Depends(get_current_user)]) -> str:
    """FastAPI dependency — retorna user_id (sub) do JWT."""
    return payload["sub"]


CurrentUserId = Annotated[str, Depends(get_user_id)]


def get_db(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_security)],
) -> Client:
    """FastAPI dependency — cliente Supabase autenticado com o JWT do usuário.

    Cada request recebe um cliente novo cujas queries carregam
    `Authorization: Bearer <jwt>` e portanto são avaliadas pelas políticas
    RLS do Postgres. Esta é a camada real de defesa de multi-tenancy.

    O token é validado primeiro pelo `HTTPBearer` (formato) e em seguida pela
    propagação para o PostgREST, que rejeita JWTs inválidos no banco.
    Não é necessário re-decodificar aqui — quando o handler também depende de
    `CurrentUserId`, a validação criptográfica já ocorreu via `get_user_id`.
    """
    return get_supabase_user(credentials.credentials)


DbClient = Annotated[Client, Depends(get_db)]
