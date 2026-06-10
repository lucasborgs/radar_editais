"""Radar unificado (Layer 2 do matching multi-quadrante, spec §3.8).

Junta os dois matchers que já existem — HybridMatch (eventos: edital/desafio/
programa) e investor_match (entidade: investidor) — num ÚNICO ranking. É a
costura "match = produto": normaliza scores heterogêneos, anexa o sinal "por que
agora" (countdown de deadline p/ evento, força-de-tese p/ entidade) e aplica um
cap por quadrante (anti-inundação).

Aditivo e isolado (de-risk): NÃO toca HybridMatchService nem match_investidores —
apenas orquestra. `/match` e `/match/investidores` seguem existindo intactos.

MVP ingênuo por design (a spec autoriza): ranking = score + cap por quadrante; o
`why_now` é sinal de display, não fator de ranking. Afinar o ranking unificado é
trabalho da fase dedicada de matching.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion: funde rankings de fontes com escalas de score
# INCOMPARÁVEIS (evento ~4-6 penalizado vs entidade ~7-9 LLM-generoso) usando só
# a POSIÇÃO dentro de cada lista, não a nota crua. score_rrf = Σ 1/(k + rank).
# k=60 é a convenção (amortece o peso desproporcional das 1ªs posições). É a
# normalização "simples e assertiva" do MVP — robusta a outlier, sem calibração,
# sem número mágico. Magnitude crua fica no payload p/ display. Refino futuro
# (pesos por quadrante, floor de qualidade) no BACKLOG.
_RRF_K = 60


def _why_now_event(edital_id: str) -> str:
    """Sinal de urgência de um evento via core.temporal (defensivo: '' em falha)."""
    try:
        from core.temporal import temporal_context
        ctx = temporal_context(edital_id)
    except Exception:
        return ""
    if ctx is None:
        return ""
    if ctx.deadline is None:
        return "Fluxo contínuo (sem prazo fixo)"
    if ctx.expired:
        return "Encerrado"
    d = ctx.days_remaining
    if d is None:
        return ""
    return "Encerra hoje" if d == 0 else f"Encerra em {d} dia{'s' if d != 1 else ''}"


def _event_item(m: dict) -> dict:
    """Normaliza um match de evento (HybridMatch) ao item comum do radar."""
    eid = m.get("id", "")
    return {
        "id": eid,
        "kind_class": "evento",
        "opportunity_type": m.get("opportunity_type", "edital"),
        "title": m.get("title", ""),
        "score": float(m.get("score") or 0.0),
        "why_now": _why_now_event(eid),
        "payload": m,
    }


def _entity_item(m: dict) -> dict:
    """Normaliza um match de entidade (investor_match) ao item comum do radar."""
    return {
        "id": m.get("id", ""),
        "kind_class": "entidade",
        "opportunity_type": "investidor",
        "title": m.get("name", ""),
        "score": float(m.get("score") or 0.0),
        "why_now": "Match de tese (sempre aberto)",
        "payload": m,
    }


def _apply_cap(items: list[dict], per_type_cap: int | None) -> list[dict]:
    """Mantém a ordem (por score), limitando a `per_type_cap` itens por
    opportunity_type (anti-inundação). None → sem cap."""
    if not per_type_cap or per_type_cap <= 0:
        return items
    seen: dict[str, int] = {}
    kept: list[dict] = []
    for it in items:
        t = it["opportunity_type"]
        if seen.get(t, 0) >= per_type_cap:
            continue
        seen[t] = seen.get(t, 0) + 1
        kept.append(it)
    return kept


def _rank_within_source(items: list[dict]) -> None:
    """Atribui `rank` (1-based, por score desc) e `rrf` (1/(k+rank)) in-place.
    Cada lista-fonte é rankeada SEPARADAMENTE — é o que torna escalas
    incomparáveis comparáveis."""
    for rank, it in enumerate(sorted(items, key=lambda x: x["score"], reverse=True), start=1):
        it["rank"] = rank
        it["rrf"] = 1.0 / (_RRF_K + rank)


def merge_radar(
    events: list[dict],
    entities: list[dict],
    *,
    top_k: int = 10,
    per_type_cap: int | None = None,
) -> dict:
    """Funde listas heterogêneas num ranking único via RRF. PURO (sem I/O além do
    `why_now` de evento, que degrada gracioso). Rankeia cada fonte, ordena pelo
    score RRF (desempate: score cru desc, p/ determinismo), aplica o cap por
    quadrante e trunca em top_k."""
    evs = [_event_item(m) for m in (events or [])]
    ents = [_entity_item(m) for m in (entities or [])]
    _rank_within_source(evs)        # eventos rankeados entre si
    _rank_within_source(ents)       # entidades rankeadas entre si

    items = evs + ents
    items.sort(key=lambda x: (x["rrf"], x["score"]), reverse=True)
    items = _apply_cap(items, per_type_cap)[:top_k]

    counts: dict[str, int] = {}
    for it in items:
        counts[it["opportunity_type"]] = counts.get(it["opportunity_type"], 0) + 1
    return {"radar": items, "counts": counts}


def build_radar(
    profile,
    *,
    top_k: int = 10,
    per_type_cap: int | None = None,
    workspace_id: str | None = None,
) -> dict:
    """Orquestra os 2 matchers reais e funde. Cada matcher degrada para [] em
    falha (sem LLM/índice/diretório) — o radar nunca levanta por isso."""
    try:
        from core.hybrid_match_service import HybridMatchService
        events = HybridMatchService().match(
            profile=profile, top_k=top_k, workspace_id=workspace_id,
        ) or []
    except Exception as e:
        logger.warning("radar: match de eventos falhou: %s", e)
        events = []
    try:
        from core.investor_match import match_investidores
        entities = match_investidores(profile, top_k=top_k) or []
    except Exception as e:
        logger.warning("radar: match de entidades falhou: %s", e)
        entities = []

    return merge_radar(events, entities, top_k=top_k, per_type_cap=per_type_cap)


__all__ = ["build_radar", "merge_radar"]
