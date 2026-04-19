"""
Auth utilities — verificação de JWT emitido pelo Supabase Auth.

Fluxo:
  1. Frontend usa @supabase/supabase-js → signInWithOtp(email) para magic link
  2. Supabase envia o email, usuário clica e recebe JWT
  3. Frontend envia JWT em Authorization: Bearer <token>
  4. Backend verifica com SUPABASE_JWT_SECRET
"""
import os
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_security)],
) -> dict:
    """FastAPI dependency — retorna payload do JWT do usuário autenticado."""
    return _decode_token(credentials.credentials)


def get_user_id(payload: Annotated[dict, Depends(get_current_user)]) -> str:
    """FastAPI dependency — retorna user_id (sub) do JWT."""
    return payload["sub"]


CurrentUserId = Annotated[str, Depends(get_user_id)]
