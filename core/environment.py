"""Contrato de identidade e segurança dos ambientes do Radar.

Este módulo não importa ``config`` de propósito: ele precisa carregar e validar o
perfil antes de módulos que leem variáveis no import. Nunca imprime credenciais.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "db"}


class Environment(str, Enum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"
    UNKNOWN = "unknown"


class DatabaseTargetError(RuntimeError):
    """O processo está prestes a operar no banco errado ou não identificado."""


@dataclass(frozen=True)
class DatabaseIdentity:
    environment: Environment
    database_host: str | None
    supabase_host: str | None
    project_ref: str | None
    is_local: bool
    is_mixed: bool


def load_environment_profile() -> Path | None:
    """Carrega um perfil sem sobrescrever variáveis já exportadas pelo runtime.

    Ordem: ``ENV_FILE`` explícito, ``.env.<ENVIRONMENT>`` e, durante a migração,
    o ``.env`` legado. Arquivos ausentes são ignorados.
    """
    explicit = os.getenv("ENV_FILE")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    declared = os.getenv("ENVIRONMENT", "").strip().lower()
    if declared:
        candidates.append(ROOT / f".env.{declared}")
    candidates.append(ROOT / ".env")
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


def resolve_environment() -> Environment:
    raw = os.getenv("ENVIRONMENT", "").strip().lower()
    if raw:
        try:
            return Environment(raw)
        except ValueError:
            return Environment.UNKNOWN

    hosts = [host for host in (_host(os.getenv("DATABASE_URL")), _host(os.getenv("SUPABASE_URL"))) if host]
    if not hosts or all(_is_local_host(host) for host in hosts):
        return Environment.LOCAL
    # Credencial remota sem identidade explícita nunca é inferida como staging/prod.
    return Environment.UNKNOWN


def database_identity() -> DatabaseIdentity:
    database_host = _host(os.getenv("DATABASE_URL"))
    supabase_host = _host(os.getenv("SUPABASE_URL"))
    hosts = [host for host in (database_host, supabase_host) if host]
    is_local = bool(hosts) and all(_is_local_host(host) for host in hosts)
    is_mixed = bool(hosts) and any(_is_local_host(host) for host in hosts) and not is_local
    return DatabaseIdentity(
        environment=resolve_environment(),
        database_host=database_host,
        supabase_host=supabase_host,
        project_ref=_project_ref(supabase_host),
        is_local=is_local,
        is_mixed=is_mixed,
    )


def read_environment_metadata() -> dict[str, Any]:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise DatabaseTargetError("DATABASE_URL ausente; não é possível validar a sentinela.")
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                "select environment, project_ref, schema_version, dataset_version "
                "from public.environment_metadata where id = true"
            )
            row = cur.fetchone()
    except Exception as exc:
        raise DatabaseTargetError(
            "Não foi possível ler public.environment_metadata; aplique a migration 040."
        ) from exc
    if not row:
        raise DatabaseTargetError("A sentinela environment_metadata não foi inicializada.")
    return dict(
        zip(
            ("environment", "project_ref", "schema_version", "dataset_version"),
            row,
            strict=True,
        )
    )


def assert_database_target(
    operation: str, *, allow_uninitialized_sentinel: bool = False
) -> dict[str, Any]:
    """Falha fechada antes de uma mutação operada por CLI."""
    identity = database_identity()
    env = identity.environment
    if env is Environment.UNKNOWN:
        raise DatabaseTargetError(
            f"{operation}: ENVIRONMENT ausente ou inválido para um alvo remoto."
        )
    if not os.getenv("DATABASE_URL"):
        raise DatabaseTargetError(f"{operation}: DATABASE_URL ausente.")
    if identity.is_mixed:
        raise DatabaseTargetError(
            f"{operation}: DATABASE_URL e SUPABASE_URL pertencem a localidades diferentes."
        )

    if env in {Environment.LOCAL, Environment.TEST} and not identity.is_local:
        raise DatabaseTargetError(f"{operation}: {env.value} só pode mutar um banco local.")
    if env in {Environment.STAGING, Environment.PRODUCTION} and identity.is_local:
        raise DatabaseTargetError(f"{operation}: {env.value} não pode apontar para localhost.")

    try:
        metadata = read_environment_metadata()
    except DatabaseTargetError:
        if not allow_uninitialized_sentinel:
            raise
        metadata = {
            "environment": env.value,
            "project_ref": identity.project_ref or "local",
            "schema_version": "uninitialized",
            "dataset_version": "uninitialized",
        }
    if metadata["environment"] != env.value:
        raise DatabaseTargetError(
            f"{operation}: ambiente declarado={env.value}, sentinela={metadata['environment']}."
        )
    if identity.project_ref and metadata["project_ref"] != identity.project_ref:
        raise DatabaseTargetError(
            f"{operation}: project_ref da URL não coincide com a sentinela."
        )

    if env is Environment.PRODUCTION:
        if os.getenv("ALLOW_PRODUCTION_MUTATION") != "1":
            raise DatabaseTargetError(
                f"{operation}: produção exige ALLOW_PRODUCTION_MUTATION=1."
            )
        expected = str(metadata["project_ref"])
        if os.getenv("CONFIRM_PROJECT_REF") != expected:
            raise DatabaseTargetError(
                f"{operation}: produção exige CONFIRM_PROJECT_REF={expected}."
            )
    return metadata


def assert_runtime_environment(component: str) -> dict[str, Any] | None:
    """Valida o alvo no boot sem conceder autorização para mutá-lo."""
    identity = database_identity()
    configured = bool(os.getenv("DATABASE_URL") or os.getenv("SUPABASE_URL"))
    if not configured:
        return None
    if identity.environment is Environment.UNKNOWN:
        raise DatabaseTargetError(
            f"{component}: alvo remoto sem ENVIRONMENT explícito; boot recusado."
        )
    if identity.is_mixed:
        raise DatabaseTargetError(
            f"{component}: DATABASE_URL e SUPABASE_URL misturam alvos local/remoto."
        )
    if identity.environment in {Environment.LOCAL, Environment.TEST} and not identity.is_local:
        raise DatabaseTargetError(
            f"{component}: {identity.environment.value} não pode usar credenciais remotas."
        )
    if identity.environment in {Environment.STAGING, Environment.PRODUCTION} and identity.is_local:
        raise DatabaseTargetError(
            f"{component}: {identity.environment.value} não pode usar credenciais locais."
        )
    metadata = read_environment_metadata()
    if metadata["environment"] != identity.environment.value:
        raise DatabaseTargetError(
            f"{component}: ambiente declarado={identity.environment.value}, "
            f"sentinela={metadata['environment']}."
        )
    if identity.project_ref and metadata["project_ref"] != identity.project_ref:
        raise DatabaseTargetError(f"{component}: project_ref diverge da sentinela.")
    return metadata


def initialize_environment_metadata(environment: Environment, project_ref: str) -> None:
    """Bootstrap explícito da sentinela; remoto requer opt-in separado."""
    identity = database_identity()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise DatabaseTargetError("DATABASE_URL ausente.")
    if identity.is_mixed:
        raise DatabaseTargetError("Bootstrap recusado: credenciais local/remoto misturadas.")
    if not identity.is_local and os.getenv("ALLOW_ENVIRONMENT_INITIALIZATION") != "1":
        raise DatabaseTargetError(
            "Bootstrap remoto exige ALLOW_ENVIRONMENT_INITIALIZATION=1."
        )
    if environment is Environment.PRODUCTION:
        if os.getenv("ALLOW_PRODUCTION_MUTATION") != "1":
            raise DatabaseTargetError("Bootstrap de produção exige ALLOW_PRODUCTION_MUTATION=1.")
        if os.getenv("CONFIRM_PROJECT_REF") != project_ref:
            raise DatabaseTargetError("CONFIRM_PROJECT_REF não coincide com o projeto de produção.")

    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """insert into public.environment_metadata
                   (id, environment, project_ref, schema_version, dataset_version)
               values (true, %s, %s, '040', %s)
               on conflict (id) do update set
                   environment = excluded.environment,
                   project_ref = excluded.project_ref,
                   schema_version = excluded.schema_version,
                   dataset_version = excluded.dataset_version,
                   updated_at = now()""",
            (environment.value, project_ref, f"{environment.value}-seed-v1"),
        )


def redacted_environment_report() -> dict[str, Any]:
    identity = database_identity()
    report: dict[str, Any] = {
        "environment": identity.environment.value,
        "database_host": identity.database_host,
        "supabase_host": identity.supabase_host,
        "project_ref": identity.project_ref,
        "target": (
            "mixed"
            if identity.is_mixed
            else "local"
            if identity.is_local
            else "remote"
            if identity.database_host or identity.supabase_host
            else "unset"
        ),
        "credentials": {
            name: bool(os.getenv(name))
            for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY")
        },
    }
    try:
        report["sentinel"] = read_environment_metadata()
        report["valid"] = report["sentinel"]["environment"] == identity.environment.value
    except DatabaseTargetError as exc:
        report["sentinel_error"] = str(exc)
        report["valid"] = not bool(os.getenv("DATABASE_URL"))
    return report


def _host(url: str | None) -> str | None:
    if not url:
        return None
    return urlparse(url).hostname


def _is_local_host(host: str) -> bool:
    return host.lower() in LOCAL_HOSTS or host.lower().endswith(".local")


def _project_ref(supabase_host: str | None) -> str | None:
    if not supabase_host or _is_local_host(supabase_host):
        return "local"
    suffix = ".supabase.co"
    if supabase_host.endswith(suffix):
        return supabase_host[: -len(suffix)].split(".")[-1]
    return None
