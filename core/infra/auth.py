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

from core.infra.db import get_supabase_user
from supabase import Client

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
SUPABASE_JWT_AUDIENCE = "authenticated"

# DEMO_MODE: bypass de login (decisão "demo pública"). Quando ligado, as rotas
# autenticadas IGNORAM o JWT, operam sobre um único workspace existente e usam
# o cliente service-role (RLS bypassed). Desligado (default) → auth normal.
# Reversível: basta remover a env var. Ver _demo_identity().
DEMO_MODE = os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")

# Em DEMO_MODE o header Authorization é opcional (senão o HTTPBearer devolve 403
# "Not authenticated" antes mesmo de chegar no handler).
_security = HTTPBearer(auto_error=not DEMO_MODE)

# Cache da chave pública EC (evita buscar o JWKS a cada request).
_ec_public_key: EllipticCurvePublicKey | None = None

# Identidade demo resolvida sob demanda (user_id + workspace_id), cacheada.
_demo_identity_cache: dict | None = None


def _demo_identity() -> dict:
    """Resolve (user_id, workspace_id) do workspace demo via service-role.

    DEMO_MODE serve o app sem login: todas as requisições operam sobre um único
    workspace já existente (workspaces.user_id tem FK pra auth.users, então não
    dá pra fabricar um user fake). Por padrão usa o workspace mais recente; fixe
    via env DEMO_WORKSPACE_ID se houver mais de um. Cacheado por processo.
    """
    global _demo_identity_cache
    if _demo_identity_cache is not None:
        return _demo_identity_cache

    from core.infra.db import get_supabase_service
    db = get_supabase_service()
    q = db.table("workspaces").select("id, user_id")
    wid = os.getenv("DEMO_WORKSPACE_ID")
    if wid:
        q = q.eq("id", wid)
    else:
        q = q.order("created_at", desc=True).limit(1)
    res = q.execute()
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "DEMO_MODE ativo mas nenhum workspace encontrado. Faça login uma "
                "vez para criar um, ou defina DEMO_WORKSPACE_ID."
            ),
        )
    row = res.data[0]
    _demo_identity_cache = {"user_id": row["user_id"], "workspace_id": row["id"]}
    logger.info("DEMO_MODE: identidade resolvida (workspace_id=%s)", row["id"])
    return _demo_identity_cache


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
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_security)],
) -> dict:
    """FastAPI dependency — retorna payload do JWT do usuário autenticado.

    Em DEMO_MODE ignora o token e devolve a identidade demo, para que o app
    funcione sem login.
    """
    if DEMO_MODE:
        return {"sub": _demo_identity()["user_id"], "demo": True}
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return _decode_token(credentials.credentials)


def get_user_id(payload: Annotated[dict, Depends(get_current_user)]) -> str:
    """FastAPI dependency — retorna user_id (sub) do JWT."""
    return payload["sub"]


CurrentUserId = Annotated[str, Depends(get_user_id)]


# ---------------------------------------------------------------------------
# Gate de operador (admin) — decisão de produto 2026-07-03: a Descoberta é
# ferramenta do OPERADOR do sistema, não do cliente final. ADMIN_EMAILS (CSV)
# define quem é operador; vazio = ninguém (fail-closed).
# ---------------------------------------------------------------------------

def _admin_emails() -> set[str]:
    """Lê ADMIN_EMAILS em call-time (não no import) — testável e ajustável
    sem restart de módulo."""
    return {
        e.strip().lower()
        for e in os.getenv("ADMIN_EMAILS", "").split(",")
        if e.strip()
    }


def is_admin_payload(payload: dict) -> bool:
    """True quando o e-mail do JWT está na allowlist ADMIN_EMAILS."""
    email = (payload.get("email") or "").strip().lower()
    return bool(email) and email in _admin_emails()


def get_admin_user_id(payload: Annotated[dict, Depends(get_current_user)]) -> str:
    """FastAPI dependency — user_id (sub) SOMENTE para operadores.

    Fail-closed: sem ADMIN_EMAILS configurado, ou JWT sem e-mail na allowlist,
    responde 403. Em DEMO_MODE o payload não tem e-mail → também 403 (a fila
    de descoberta não faz parte da demo pública).
    """
    if not is_admin_payload(payload):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito ao operador do sistema",
        )
    return payload["sub"]


AdminUserId = Annotated[str, Depends(get_admin_user_id)]


# Bearer opcional: nunca levanta por header ausente/malformado (auto_error=False),
# para as rotas com auth OPCIONAL (ex.: /match/radar — delta B2). Anônimo segue
# com pesos globais default; logado mantém o comportamento atual.
_optional_security = HTTPBearer(auto_error=False)


def get_optional_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_security)],
) -> str | None:
    """FastAPI dependency — user_id quando há JWT válido, senão None.

    Diferente de `get_user_id`: SEM Authorization ou com token inválido/expirado
    retorna None em vez de 401. Usado só onde a auth é opcional (porta pública).
    Em DEMO_MODE devolve a identidade demo (consistente com get_current_user).
    """
    if DEMO_MODE:
        return _demo_identity()["user_id"]
    if credentials is None:
        return None
    try:
        return _decode_token(credentials.credentials)["sub"]
    except HTTPException:
        # Token inválido/expirado numa rota pública → trata como anônimo.
        return None


OptionalUserId = Annotated[str | None, Depends(get_optional_user_id)]


def get_db(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_security)],
) -> Client:
    """FastAPI dependency — cliente Supabase autenticado com o JWT do usuário.

    Cada request recebe um cliente novo cujas queries carregam
    `Authorization: Bearer <jwt>` e portanto são avaliadas pelas políticas
    RLS do Postgres. Esta é a camada real de defesa de multi-tenancy.

    O token é validado primeiro pelo `HTTPBearer` (formato) e em seguida pela
    propagação para o PostgREST, que rejeita JWTs inválidos no banco.
    Não é necessário re-decodificar aqui — quando o handler também depende de
    `CurrentUserId`, a validação criptográfica já ocorreu via `get_user_id`.

    Em DEMO_MODE não há JWT: usa o cliente service-role (RLS bypassed). O
    scoping continua correto porque os handlers filtram por workspace_id
    explicitamente; o RLS é só uma segunda camada, dispensável no modo demo.
    """
    if DEMO_MODE:
        from core.infra.db import get_supabase_service
        return get_supabase_service()
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return get_supabase_user(credentials.credentials)


DbClient = Annotated[Client, Depends(get_db)]


def get_optional_db(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_security)],
) -> Client | None:
    """FastAPI dependency — cliente Supabase autenticado quando há JWT, senão None.

    Par de `get_optional_user_id` para rotas com auth opcional (B2): sem header
    (anônimo) não há cliente per-request, então devolve None — o handler resolve
    workspace só no caminho autenticado. Em DEMO_MODE usa o service-role.
    """
    if DEMO_MODE:
        from core.infra.db import get_supabase_service
        return get_supabase_service()
    if credentials is None:
        return None
    return get_supabase_user(credentials.credentials)


OptionalDbClient = Annotated[Client | None, Depends(get_optional_db)]
