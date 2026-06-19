"""Radar unificado (Layer 2 do matching multi-quadrante, spec §3.8).

Junta os matchers que já existem — HybridMatch (eventos: edital/desafio/programa),
investor_match (entidade: investidor) e programa_match (entidade: programa
recorrente) — num ÚNICO ranking. É a costura "match = produto": normaliza scores
heterogêneos, anexa o sinal "por que agora" (countdown de deadline p/ evento,
força-de-tese p/ entidade) e aplica um cap por quadrante (anti-inundação).

Aditivo e isolado (de-risk): NÃO toca HybridMatchService, match_investidores nem
match_programas — apenas orquestra. `/match`, `/match/investidores` e
`/match/programas` seguem existindo intactos.

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
# (pesos por quadrante) no BACKLOG.
_RRF_K = 60

# Floor de qualidade (tier "fraco"): RRF funde por rank, então o rank-1 de uma
# lista fraca boiaria no topo. Rebaixamos (NÃO eliminamos — entidade nunca some,
# spec §3.8) os matches fracos pra um segundo tier. Sinais por fonte:
#   • evento: reusa o flag `eligible` do HybridMatch (Stage 1 já tem gate) — sem
#     número novo. eligible=False = aproximado = fraco.
#   • entidade: não tem gate → floor de score. O scorer LLM ancora alto (fits bons
#     ~8-9.5); abaixo de _ENTITY_FLOOR = "morno". É o único knob — calibrar com dado.
_ENTITY_FLOOR = 6.0


def _why_now_event(edital_id: str) -> str:
    """Sinal de urgência de um evento via core.kg.temporal (defensivo: '' em falha)."""
    try:
        from core.kg.temporal import temporal_context
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
        "verificacao": m.get("verificacao", "verificado"),
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
        # diretório de investidores é curado à mão — sempre verificado
        "verificacao": "verificado",
        "title": m.get("name", ""),
        "score": float(m.get("score") or 0.0),
        "why_now": "Match de tese (sempre aberto)",
        "payload": m,
    }


def _programa_item(m: dict) -> dict:
    """Normaliza um match de programa recorrente (programa_match) ao item comum.
    opportunity_type `programa-recorrente` (NÃO `programa`, que é o evento datado)
    p/ o cap e o display distinguirem a entidade-âncora do edital-programa."""
    return {
        "id": m.get("id", ""),
        "kind_class": "entidade",
        "opportunity_type": "programa-recorrente",
        # diretório de programas é curado à mão — sempre verificado
        "verificacao": "verificado",
        "title": m.get("name", ""),
        "score": float(m.get("score") or 0.0),
        "why_now": "Programa recorrente (edição periódica)",
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


def _is_weak(item: dict, entity_floor: float) -> bool:
    """Match fraco (tier inferior): evento aproximado (eligible=False, gate do
    HybridMatch) ou entidade abaixo do floor de score. Nunca remove — só rebaixa."""
    if item["kind_class"] == "evento":
        return item["payload"].get("eligible") is False
    return item["score"] < entity_floor


def merge_radar(
    events: list[dict],
    entities: list[dict],
    programas: list[dict] | None = None,
    *,
    top_k: int = 10,
    per_type_cap: int | None = None,
    entity_floor: float = _ENTITY_FLOOR,
) -> dict:
    """Funde listas heterogêneas num ranking único via RRF + floor de qualidade.
    PURO (sem I/O além do `why_now` de evento, que degrada gracioso).

    Rankeia cada fonte (RRF), parte em dois tiers (forte/fraco — fracos rebaixados,
    não removidos), ordena cada tier por RRF (desempate: score cru desc), concatena
    fortes→fracos, aplica o cap por quadrante e trunca em top_k. Cada item ganha
    `tier ∈ {forte, fraco}` para o display.
    """
    evs = [_event_item(m) for m in (events or [])]
    ents = [_entity_item(m) for m in (entities or [])]
    progs = [_programa_item(m) for m in (programas or [])]
    _rank_within_source(evs)        # eventos rankeados entre si
    _rank_within_source(ents)       # entidades (investidor) rankeadas entre si
    _rank_within_source(progs)      # programas recorrentes rankeados entre si

    strong: list[dict] = []
    weak: list[dict] = []
    for it in evs + ents + progs:
        weak_flag = _is_weak(it, entity_floor)
        it["tier"] = "fraco" if weak_flag else "forte"
        (weak if weak_flag else strong).append(it)

    sort_key = lambda x: (x["rrf"], x["score"])  # noqa: E731
    strong.sort(key=sort_key, reverse=True)
    weak.sort(key=sort_key, reverse=True)
    items = _apply_cap(strong + weak, per_type_cap)[:top_k]

    counts: dict[str, int] = {}
    for it in items:
        counts[it["opportunity_type"]] = counts.get(it["opportunity_type"], 0) + 1
    return {"radar": items, "counts": counts}


def build_radar(
    profile,
    *,
    top_k: int = 10,
    per_type_cap: int | None = None,
    entity_floor: float = _ENTITY_FLOOR,
    workspace_id: str | None = None,
) -> dict:
    """Orquestra os 2 matchers reais e funde. Cada matcher degrada para [] em
    falha (sem LLM/índice/diretório) — o radar nunca levanta por isso."""
    try:
        from core.services.hybrid_match_service import HybridMatchService
        events = HybridMatchService().match(
            profile=profile, top_k=top_k, workspace_id=workspace_id,
        ) or []
    except Exception as e:
        logger.warning("radar: match de eventos falhou: %s", e)
        events = []
    try:
        from core.services.investor_match import match_investidores
        entities = match_investidores(profile, top_k=top_k) or []
    except Exception as e:
        logger.warning("radar: match de entidades falhou: %s", e)
        entities = []
    try:
        from core.services.programa_match import match_programas
        programas = match_programas(profile, top_k=top_k) or []
    except Exception as e:
        logger.warning("radar: match de programas falhou: %s", e)
        programas = []

    return merge_radar(
        events, entities, programas,
        top_k=top_k, per_type_cap=per_type_cap, entity_floor=entity_floor,
    )


__all__ = ["build_radar", "merge_radar"]
