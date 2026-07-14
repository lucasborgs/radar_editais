"""Materializa evidência aprovada no contrato já aceito pela fonte `web`."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from config import BRONZE_DIR
from core.web_identity import normalize_web_url, web_url_hash


def materialize_approved_evidence(opportunity: dict, evidence: dict | None = None) -> str:
    """Grava bronze web compatível e retorna o `edital_id` nativo.

    Não chama crawler, adapter, gold ou RAG; a promoção enfileira os jobs já
    existentes usando o identificador retornado.
    """
    evidence = evidence or opportunity.get("raw") or {}
    page = evidence.get("page") or {}
    url = normalize_web_url(evidence.get("canonical_url") or opportunity["url"])
    url_hash = web_url_hash(url)
    entry = {
        "url": url, "url_hash": url_hash,
        "title": opportunity.get("title") or evidence.get("title") or "Descoberta promovida",
        "html": page.get("html") or "",
        "texto_cru": page.get("text") or evidence.get("texto_cru") or opportunity.get("descricao") or "",
        "descricao": opportunity.get("descricao") or "", "agency": opportunity.get("agency") or "",
        "fonte": opportunity.get("fonte") or "Web (promoção)", "status": "ABERTA",
        "tema": opportunity.get("tema") or "", "opportunity_type": opportunity.get("opportunity_type") or "edital",
        "prazo_envio": opportunity.get("prazo_envio") or "", "publico_alvo": opportunity.get("publico_alvo") or "",
        "verificacao": "promovido", "data_extracao": datetime.now(timezone.utc).date().isoformat(),
    }
    target = BRONZE_DIR / "web_raw"
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    (target / f"web_promoted_{stamp}.json").write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
    return f"web:{url_hash}"
