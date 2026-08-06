"""Canal de descoberta via Deep Research (spec discovery-deep-research.md).

CANAL COMPLEMENTAR aos scrapers determinísticos: usa o engine real
`radar.core.deep_research.run_deep_research` (subagente web_search + fetch_url,
sempre com citação) para ampliar a investigação de linhas de crédito, fontes
desestruturadas, desafios corporativos, aceleradoras/incubadoras, ICTs e novas
fontes de fomento.

Contrato do canal (para o staging `discovered_opportunities`):

    pesquisa (targets do doc) → pacote de evidências → staging (pending)
    → revisão humana → documento canônico → gold/KG → catálogo/Radar/RAG

Deep Research NUNCA publica no catálogo/KG: cada fonte citada vira 1 candidato
na fila de revisão. O pacote de evidências preserva URL, citações, data, fonte,
campos ausentes, conflitos e confiança (spec §2); fatos contraditórios nunca
são resolvidos silenciosamente (ficam `conflicts` para o gate humano).

Determinístico/fail-open (aceite 5): sem engine/credencial/busca, degrada para
[] — o canal open_search/scrapers segue intacto.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Teto defensivo do texto guardado por candidato (mesmo do _extract).
_TEXTO_CRU_CAP = 60_000

_STAGING_FIELD_NAMES = (
    "title", "prazo_envio", "publico_alvo", "descricao", "status",
    "tema", "agency",
)

# Default do backend do subagente (engine deep_research.py usa anthropic).
_DEFAULT_PROVIDER = "anthropic"


@dataclass
class DeepResearchSource:
    """Uma fonte citada pelo agente (contrato de entrada do canal)."""

    url: str
    title: str
    snippet: str = ""


def _confidence(n_citations: int) -> str:
    if n_citations >= 2:
        return "high"
    if n_citations == 1:
        return "medium"
    return "low"


def _missing_fields(record: dict) -> list[str]:
    return [name for name in _STAGING_FIELD_NAMES if not str(record.get(name) or "").strip()]


def _build_record(
    source: DeepResearchSource,
    *,
    answer: str,
    target: dict,
    citations: list[DeepResearchSource],
    researched_at: datetime,
    provider: str,
) -> dict:
    """Monta o registro de staging + pacote de evidências com o bloco
    `deep_research` (proveniência da síntese)."""
    from radar.core.services.discovery_evidence import build_evidence_package
    from radar.core.web_identity import normalize_web_url, web_url_hash

    snippet = (source.snippet or "").strip()
    url = normalize_web_url(source.url)
    record = {
        "url": url,
        "url_hash": web_url_hash(url),
        "title": (source.title or "Descoberta via Deep Research")[:240],
        "texto_cru": (snippet or answer)[:_TEXTO_CRU_CAP],
        "descricao": snippet[:600],
        "prazo_envio": "",
        "publico_alvo": "",
        "status": "",
        "tema": "",
        "opportunity_type": target.get("type_hint") or "edital",
        "agency": "",
        "fonte": "Deep Research",
        "verificacao": "provisorio",
        "data_extracao": researched_at.date().isoformat(),
    }
    package = build_evidence_package(record, collector="deep_research")
    package["deep_research"] = {
        "researched_at": researched_at.isoformat(),
        "target_key": target["key"],
        "question": target.get("brief")[:500],
        "provider": provider,
        "answer": answer[:8000],
        "citations": [
            {"title": c.title[:200], "url": c.url, "snippet": (c.snippet or "")[:300]}
            for c in citations
        ][:10],
        "confidence": _confidence(len(citations)),
        "missing_fields": _missing_fields(record),
        "conflicts": [],
        "conflicts_resolution": "staged_for_review",
        "relationship_to_source": "sintese_com_citacao",
    }
    record["evidence_package"] = package
    return record


def run_deep_research_channel(
    targets: list[dict],
    *,
    max_findings: int = 10,
    provider: str | None = None,
    exclude_urls: set[str] | None = None,
) -> list[dict]:
    """Roda o canal Deep Research e devolve registros prontos pro staging.

    Cada fonte citada pelo agente vira 1 candidato (1 URL = 1 registro, mesmo
    contrato da torneira). Dedup contra o ledger/KG (`exclude_urls`) e os
    domínios sociais/dedicados. Fail-open: qualquer falha degrada o alvo (ou o
    canal inteiro) para [] sem levantar.

    `provider`: backend do subagente (env DISCOVERY_DEEP_RESEARCH_PROVIDER ou
    default do engine). `exclude_urls`: URLs já vistas (ledger ∪ KG ∪ rodada).
    """
    if not targets or max_findings <= 0:
        return []
    try:
        from radar.core import deep_research  # noqa: PLC0415
        from radar.core.ingestion.opportunity_discovery import (  # noqa: PLC0415
            _is_dedicated_source,
            _is_social,
            _known_urls,
            _norm_url,
        )
    except Exception as exc:
        logger.warning("deep_research: canal indisponível (%s)", exc)
        return []

    provider = (
        provider
        or os.getenv("DISCOVERY_DEEP_RESEARCH_PROVIDER", "").strip()
        or _DEFAULT_PROVIDER
    )
    known = set(exclude_urls) if exclude_urls is not None else set(_known_urls())
    seen: set[str] = set()
    researched_at = datetime.now(timezone.utc)
    findings: list[dict] = []

    for target in targets:
        if len(findings) >= max_findings:
            break
        try:
            result = deep_research.run_deep_research(
                target["brief"], provider=provider,
            )
        except Exception as exc:
            logger.warning(
                "deep_research: falha no alvo %s (%s) — segue o próximo",
                target.get("key"), exc,
            )
            continue
        if result.stop_reason == "error" or not (result.answer or "").strip():
            logger.info(
                "deep_research: alvo %s sem resposta (stop=%s) — skip",
                target.get("key"), result.stop_reason,
            )
            continue

        citations = [
            DeepResearchSource(url=s.url, title=s.title, snippet=s.snippet)
            for s in result.sources if s.url
        ]
        for source in citations:
            if len(findings) >= max_findings:
                break
            norm = _norm_url(source.url)
            if (not norm or norm in known or norm in seen
                    or _is_social(source.url) or _is_dedicated_source(source.url)):
                continue
            seen.add(norm)
            findings.append(_build_record(
                source,
                answer=result.answer, target=target, citations=citations,
                researched_at=researched_at, provider=provider,
            ))

    logger.info("deep_research: %d candidatos de %d alvos", len(findings), len(targets))
    return findings
