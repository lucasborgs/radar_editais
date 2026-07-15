#!/usr/bin/env python3
"""Gera `.env.staging-local` a partir do Supabase CLI sem revelar chaves.

O arquivo gerado é gitignored e recebe permissão 0600. Somente credenciais LLM
explicitamente permitidas são herdadas do `.env` legado; nenhuma credencial do
Supabase Cloud é copiada.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".env.staging-local"
INHERITED = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "LLM_BACKEND",
    "OPENAI_MODEL",
    "ANTHROPIC_MODEL_AGENT",
    "EMBEDDING_MODEL",
    "RERANK_BACKEND",
)


def _status_env() -> dict[str, str]:
    result = subprocess.run(
        ["supabase", "status", "-o", "env"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    values: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        parsed = shlex.split(value.strip())
        values[key.strip()] = parsed[0] if parsed else ""
    return values


def _quoted(value: str | None) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def main() -> int:
    local = _status_env()
    mapping = {
        "SUPABASE_URL": local.get("API_URL", ""),
        "SUPABASE_ANON_KEY": local.get("ANON_KEY") or local.get("PUBLISHABLE_KEY", ""),
        "SUPABASE_SERVICE_KEY": local.get("SERVICE_ROLE_KEY") or local.get("SECRET_KEY", ""),
        "SUPABASE_JWT_SECRET": local.get("JWT_SECRET", ""),
        "DATABASE_URL": local.get("DB_URL", ""),
    }
    missing = [key for key, value in mapping.items() if not value]
    if missing:
        raise SystemExit(
            "Supabase local incompleto; ausentes em `supabase status -o env`: "
            + ", ".join(missing)
        )

    legacy = dotenv_values(ROOT / ".env")
    inherited = {key: legacy.get(key, "") for key in INHERITED}
    lines = [
        "# Gerado por scripts/bootstrap_staging_local.py — não versionar.",
        "ENVIRONMENT=test",
        "INTEGRATION_TARGET=local",
        "EMBEDDING_DIMENSIONS=1536",
        *(f"{key}={_quoted(value)}" for key, value in mapping.items()),
        "DOCKER_SUPABASE_URL=http://host.docker.internal:54321",
        "DOCKER_DATABASE_URL=postgresql://postgres:postgres@host.docker.internal:54322/postgres",
        "ENV_FILE=.env.staging-local",
        "COMPOSE_PROJECT_NAME=radar-staging-local",
        "APP_CONTAINER_NAME=radar-staging-app",
        "WORKER_CONTAINER_NAME=radar-staging-worker",
        "API_PORT=8001",
        "DATA_DIR=./.data-staging",
        "DISCOVERY_CRAWL4AI_ENABLED=0",
        "DISCOVERY_DOU_ENABLED=0",
        "FRONTEND_URL=http://localhost:3000",
        "ALERT_EMAIL_TO=",
        "LANGFUSE_PUBLIC_KEY=",
        "LANGFUSE_SECRET_KEY=",
        "SENTRY_DSN=",
        *(f"{key}={_quoted(value)}" for key, value in inherited.items() if value),
        "",
    ]
    TARGET.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(TARGET, 0o600)
    print(f"Perfil local criado em {TARGET} (permissão 0600; valores não exibidos).")
    if not inherited.get("OPENAI_API_KEY"):
        print("Aviso: OPENAI_API_KEY ausente; gates conectados de LLM ficarão indisponíveis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
