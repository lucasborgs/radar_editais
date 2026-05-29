"""
Auth utilities — verificação de JWT emitido pelo Supabase Auth.

Fluxo:
  1. Frontend usa @supabase/supabase-js → signInWithOtp(email) para magic link
  2. Supabase envia o email, usuário clica e recebe JWT
  3. Frontend envia JWT em Authorization: Bearer <token>
  4. Backend verifica o JWT:
     - HS256: via SUPABASE_JWT_SECRET (Supabase CLI < 2.x)
     - ES256: via JWKS endpoint (Supabase CLI ≥ 2.x — padrão atual)
  5. Backend cria um Supabase client per-request com a anon key + esse JWT,
     de forma que todas as queries passem pelas políticas RLS do Postgres.
"""
import json
import logging
import os
import urllib.request
from typing import Annotated

import jwt
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.algorithms import ECAlgorithm

from core.db import get_supabase_user
from supabase import Client

logger = logging.getLogger(__name__)

_security = HTTPBearer()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
SUPABASE_JWT_AUDIENCE = "authenticated"

# Cache da chave pública EC (evita buscar o JWKS a cada request).
_ec_public_key: EllipticCurvePublicKey | None = None


def _load_ec_public_key() -> EllipticCurvePublicKey | None:
    """Busca o primeiro key do JWKS endpoint e retorna como objeto EC público."""
    global _ec_public_key
    if _ec_public_key is not None:
        return _ec_public_key
    jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    try:
        with urllib.request.urlopen(jwks_url, timeout=5) as resp:
            jwks = json.loads(resp.read())
        key_data = jwks["keys"][0]
        _ec_public_key = ECAlgorithm.from_jwk(key_data)
        return _ec_public_key
    except Exception as exc:
        logger.warning("Não foi possível carregar JWKS de %s: %s", jwks_url, exc)
        return None


def _decode_token(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")

    try:
        if alg == "ES256":
            key = _load_ec_public_key()
            if key is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Não foi possível carregar chave pública JWKS",
                )
            return jwt.decode(
                token,
                key,
                algorithms=["ES256"],
                audience=SUPABASE_JWT_AUDIENCE,
            )
        else:
            if not SUPABASE_JWT_SECRET:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="SUPABASE_JWT_SECRET não configurada",
                )
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
