"""
Radar Editais — FastAPI Backend

Executar da raiz do projeto:
    uvicorn backend.api:app --reload --port 8000

Docs automáticos: http://localhost:8000/docs

Este módulo é só o SHELL da aplicação: app + middleware + exception handlers +
wiring dos routers. Endpoints vivem em backend/routers/ (por domínio) e nos
routers raiz auth_routes/library_routes; dependências compartilhadas em
backend/common.py; rate limiting em backend/rate_limit.py.
"""

from core.environment import assert_runtime_environment, load_environment_profile

load_environment_profile()


import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.auth_routes import router as auth_router
from backend.library_routes import router as library_router
from backend.rate_limit import limiter
from backend.routers.applications import router as applications_router
from backend.routers.catalog import router as catalog_router
from backend.routers.conversations import router as conversations_router
from backend.routers.discovered import router as discovered_router
from backend.routers.explore import router as explore_router
from backend.routers.planning import router as planning_router
from backend.routers.profile import router as profile_router
from backend.routers.radar import router as radar_router
from backend.routers.research import router as research_router
from backend.routers.workspace import router as workspace_router
from backend.routers.writing import router as writing_router
from core.infra.logging_config import request_id_var, setup_logging

setup_logging()
logger = logging.getLogger(__name__)
assert_runtime_environment("backend API")


def _check_database_health() -> None:
    """Abre uma conexão curta e confirma que o Postgres aceita queries."""
    import psycopg

    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    from core.llm.agent_graph import shutdown_writing_runtime

    await asyncio.to_thread(shutdown_writing_runtime)


def _guard_demo_mode() -> None:
    """PR1.3 (hardening-pre-beta): DEMO_MODE bypassa auth+RLS via service-role.

    Em produção multiusuário isso colapsa todos os usuários num único workspace
    sem login — recusa o boot, salvo override deliberado.
    """
    demo = os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    env = (os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "").strip().lower()
    allow = os.getenv("DEMO_MODE_ALLOW_PROD", "").strip().lower() in ("1", "true", "yes", "on")
    if demo and env == "production" and not allow:
        raise RuntimeError(
            "DEMO_MODE=1 em produção bypassa auth+RLS (todos os usuários viram o "
            "mesmo workspace). Desligue DEMO_MODE ou sete DEMO_MODE_ALLOW_PROD=1 "
            "para forçar deliberadamente."
        )


_guard_demo_mode()

# =============================================================================
# APP + CORS
# =============================================================================

app = FastAPI(
    title="Radar Editais API",
    description="Plataforma de matching e escrita de propostas para editais de fomento (FINEP, FAPESP, FAPESC, web)",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health", include_in_schema=False)
async def health():
    try:
        await asyncio.to_thread(_check_database_health)
    except Exception as exc:  # noqa: BLE001 — health deve degradar para 503
        logger.warning("Healthcheck do Postgres falhou: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unhealthy"})
    return {"status": "ok"}


class RequestIdMiddleware:
    """Middleware ASGI puro que atribui um request_id por request.

    Usa ASGI puro (não BaseHTTPMiddleware) para que o contextvar propague
    corretamente aos logs no mesmo contexto async, e persiste o id em
    `scope["state"]` para que o exception handler de 500 — que roda acima desta
    camada na cadeia ASGI — ainda consiga lê-lo via `request.state`.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers", []))
        rid = incoming.get(b"x-request-id", b"").decode() or uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


def _allowed_origins() -> list[str]:
    # localhost sempre liberado para dev; FRONTEND_URL (CSV) adiciona origens de prod.
    defaults = ["http://localhost:3000", "http://127.0.0.1:3000",
                 "http://localhost:3003", "http://127.0.0.1:3003"]
    extra = [o.strip() for o in os.getenv("FRONTEND_URL", "").split(",") if o.strip()]
    return list(dict.fromkeys(defaults + extra))


ALLOWED_ORIGINS = _allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Registrado após o CORS → fica mais externo, atribuindo o request_id cedo.
app.add_middleware(RequestIdMiddleware)

# =============================================================================
# RATE LIMITING (slowapi) — limiter vive em backend/rate_limit.py
# =============================================================================

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Este handler roda acima do RequestIdMiddleware na cadeia ASGI, então o
    # contextvar já foi resetado — recuperamos o id persistido no scope state.
    rid = getattr(request.state, "request_id", "-")
    request_id_var.set(rid)
    # Stack trace completo fica no servidor (correlacionável por request_id);
    # o cliente recebe só uma mensagem genérica + o id para suporte.
    logger.error("Erro não tratado em %s %s", request.method, request.url.path, exc_info=exc)

    # O 500 é gerado acima do CORSMiddleware, então o header CORS precisa ser
    # reaplicado manualmente — refletindo o Origin da request se for permitido.
    origin = request.headers.get("origin", "")
    headers = {"X-Request-ID": rid}
    if origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Erro interno no servidor. Tente novamente.",
            "request_id": rid,
        },
        headers=headers,
    )


# =============================================================================
# ROUTERS
# =============================================================================

app.include_router(auth_router)
app.include_router(library_router)
app.include_router(catalog_router)
app.include_router(explore_router)
app.include_router(planning_router)
app.include_router(applications_router)
app.include_router(workspace_router)
app.include_router(writing_router)
app.include_router(conversations_router)
app.include_router(profile_router)
app.include_router(radar_router)
app.include_router(research_router)
app.include_router(discovered_router)
