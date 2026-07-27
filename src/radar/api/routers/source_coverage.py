"""Endpoint administrativo de cobertura da Descoberta (RT03-T06).

``GET /source-coverage`` — protegido por ``AdminUserId``, estritamente
read-only. Expõe o read model de RT03-T05 sem alterar staging, registry,
flags ou fontes.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from radar.core.infra.auth import AdminUserId
from radar.core.infra.db import get_supabase_service
from radar.core.kg.schema import coverage_channels, query_families
from radar.core.services.source_coverage_metrics import compute_source_coverage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/source-coverage", tags=["source-coverage"])

# ── Response models ──────────────────────────────────────────────────────


class ChannelRunMetricsOut(BaseModel):
    last_attempt: str | None = None
    last_success: str | None = None
    total_records_observed: int | None = None
    total_records_emitted: int | None = None
    total_records_staged: int | None = None
    yield_rate: float | None = None


class EditorialFunnelOut(BaseModel):
    source_key: str
    approved: int = 0
    rejected: int = 0
    pending: int = 0
    approval_rate: float | None = None
    avg_review_hours: float | None = None


class FamilyFunnelOut(BaseModel):
    family_key: str
    approved: int = 0
    rejected: int = 0
    pending: int = 0
    approval_rate: float | None = None
    avg_review_hours: float | None = None


class CoverageGapOut(BaseModel):
    source_key: str | None = None
    signal: str


class EmergingDomainOut(BaseModel):
    domain: str
    approval_count: int
    first_approved_at: str
    last_approved_at: str
    candidate_for_dedicated_monitoring: bool


class ChannelHealthOut(BaseModel):
    source_key: str
    health: str


class SourceCoverageResponse(BaseModel):
    generated_at: str
    channels: list[ChannelHealthOut]
    runs: dict[str, ChannelRunMetricsOut]
    channel_funnel: dict[str, EditorialFunnelOut]
    family_funnel: dict[str, FamilyFunnelOut]
    gaps: list[CoverageGapOut]
    emerging_domains: list[EmergingDomainOut]
    limitations: list[str]


_LIMITATIONS: list[str] = [
    "Cobertura absoluta da web é impossível de provar. "
    "Métricas refletem o que foi observado, não o universo total de oportunidades.",
    "Denominadores ausentes ou ambíguos retornam null, nunca zero fabricado.",
    "Estado healthy indica conclusão técnica do canal, "
    "não cobertura completa do portal de origem.",
    "Domínios emergentes são sinal operacional pré-beta. "
    "Não criam scraper, fonte ou promoção automática.",
    "Resultado vazio sem prova suficiente permanece unknown.",
]

# ── Helpers ──────────────────────────────────────────────────────────────


def _build_env_from_channels(channels: list[dict]) -> dict[str, str]:
    """Extrai valores de flag do ambiente real apenas para canais gated.

    Canais sem ``flag_name`` não entram no dict — o read model usa o
    ``enabled_by_default`` declarado.
    """
    env: dict[str, str] = {}
    for ch in channels:
        flag = ch.get("flag_name")
        if flag:
            val = os.environ.get(flag, "")
            if val:
                env[flag] = val
    return env


def _sanitize_error(exc: Exception) -> str:
    """Retorna mensagem de erro categórica sem expor detalhes internos."""
    logger.error("source-coverage: erro ao computar relatório", exc_info=exc)
    return "Erro ao gerar relatório de cobertura. Tente novamente."


# ── Endpoint ─────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SourceCoverageResponse,
    summary="Relatório de cobertura da Descoberta",
)
def get_source_coverage(user_id: AdminUserId):
    """Relatório de cobertura, saúde e funil dos canais de aquisição.

    Requer permissão de operador (``AdminUserId``). Estritamente read-only.
    """
    channels = coverage_channels()
    families = query_families()
    family_keys = [fam["key"] for fam in families]
    env = _build_env_from_channels(channels)

    db = get_supabase_service()

    try:
        runs_result = db.table("source_runs").select("*").execute()
        discovered_result = (
            db.table("discovered_opportunities")
            .select(
                "id,status,discovery_channel,query_family,origin_domain,"
                "created_at,reviewed_at"
            )
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=_sanitize_error(exc)
        ) from exc

    runs: list[dict] = runs_result.data or []
    discovered: list[dict] = discovered_result.data or []

    now = datetime.now(timezone.utc)

    try:
        report = compute_source_coverage(
            runs=runs,
            discovered=discovered,
            channels=channels,
            family_keys=family_keys,
            ref_dt=now,
            env=env,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=_sanitize_error(exc)
        ) from exc

    channel_health_list = [
        ChannelHealthOut(
            source_key=sk,
            health=report.channel_health.get(sk, "unknown"),
        )
        for sk in sorted(report.channel_health.keys())
    ]

    runs_out: dict[str, ChannelRunMetricsOut] = {}
    for sk, m in report.channel_runs.items():
        runs_out[sk] = ChannelRunMetricsOut(
            last_attempt=m.last_attempt.isoformat() if m.last_attempt else None,
            last_success=m.last_success.isoformat() if m.last_success else None,
            total_records_observed=m.total_records_observed,
            total_records_emitted=m.total_records_emitted,
            total_records_staged=m.total_records_staged,
            yield_rate=m.yield_rate,
        )

    return SourceCoverageResponse(
        generated_at=now.isoformat(),
        channels=channel_health_list,
        runs=runs_out,
        channel_funnel={
            sk: EditorialFunnelOut(
                source_key=f.source_key,
                approved=f.approved,
                rejected=f.rejected,
                pending=f.pending,
                approval_rate=f.approval_rate,
                avg_review_hours=f.avg_review_hours,
            )
            for sk, f in report.channel_funnel.items()
        },
        family_funnel={
            fk: FamilyFunnelOut(
                family_key=f.family_key,
                approved=f.approved,
                rejected=f.rejected,
                pending=f.pending,
                approval_rate=f.approval_rate,
                avg_review_hours=f.avg_review_hours,
            )
            for fk, f in report.family_funnel.items()
        },
        gaps=[
            CoverageGapOut(source_key=g.source_key, signal=g.signal)
            for g in report.gaps
        ],
        emerging_domains=[
            EmergingDomainOut(
                domain=d.domain,
                approval_count=d.approval_count,
                first_approved_at=d.first_approved_at.isoformat(),
                last_approved_at=d.last_approved_at.isoformat(),
                candidate_for_dedicated_monitoring=d.candidate_for_dedicated_monitoring,
            )
            for d in report.emerging_domains
        ],
        limitations=_LIMITATIONS,
    )
