"""Veredito LLM top-K (KG v2, PR7 — Estágio 2 do funil de match).

O match core segue SEM LLM no ranking (D9): o LLM entra só aqui, DEPOIS do filtro
duro (Estágio 0) e da afinidade MaxSim (Estágio 1), para produzir um veredito
ESTRUTURADO sobre cada par (empresa, oportunidade) do top-K — razões, não score:

    { racional_afinidade, red_flags_elegibilidade[], fit_mecanismo, recomendacao }

O veredito reordena SÓ dentro do top-K e alimenta a explicação do card. Input é o
KG serializado (D10: nós + arestas em linguagem natural + propriedades +
constraints + requisitos_texto — NUNCA chunks do RAG; texto do edital no veredito
é a v2 documentada). As arestas nativas são consumidas aqui (D12) — é justamente
o que o juiz de elegibilidade precisa ler.

Custo escala com K, não com o corpus: a chamada é async (task procrastinate
`compute_match_verdicts`) e cacheada por par em `match_verdicts` (migration 035),
com invalidação implícita por `input_hash` — perfil, oportunidade ou prompt
mudou ⇒ hash muda ⇒ recomputa e o upsert substitui. O card renderiza sem o
veredito e o recebe quando pronto (poll cache-only, zero LLM).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from core.services.eligibility import _reason as _constraint_reason

logger = logging.getLogger(__name__)

# Modelo do veredito = tier 3 já em produção (OPENAI_MODEL), overridável em
# separado (VERDICT_MODEL) sem mexer no tier — mesma postura do CONSTRAINTS_MODEL.
def _verdict_model() -> str:
    return os.getenv("VERDICT_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# Versão do prompt — entra no input_hash: mudar o prompt invalida o cache inteiro
# (senão vereditos velhos de um prompt antigo pareceriam frescos para sempre).
_PROMPT_VERSION = "v1"

# Caps de serialização (bound de tokens por chamada; o subgrafo de um edital tem
# dezenas de nós/arestas, não centenas — os caps são cinto de segurança).
_MAX_EDGES = 40
_MAX_CONCEITOS = 30
_MAX_TEXT_ITEMS = 12

_SYSTEM = """Você é o veredito final do radar de oportunidades de fomento à \
inovação (Estágio 2 do funil de match). Recebe o PERFIL de uma empresa e o \
SUBGRAFO de uma oportunidade (edital/chamada) já pré-selecionada por afinidade \
semântica e filtro de elegibilidade. Seu papel é EXPLICAR o fit para o usuário \
decidir — você produz razões, não pontuação.

Responda JSON com EXATAMENTE estas chaves:
- "racional_afinidade": 2-3 frases concretas sobre POR QUE o conteúdo da \
oportunidade casa (ou não) com o que a empresa faz — ancore nos pares de \
afinidade fornecidos e diga também o que NÃO conecta.
- "red_flags_elegibilidade": lista (pode ser vazia) de alertas OBJETIVOS \
extraídos dos requisitos, exclusões, constraints e relações do subgrafo — ex. \
exige parceria com ICT, restrição de porte/faturamento/UF, contrapartida. \
Frases curtas. NÃO invente: só o que está no subgrafo.
- "fit_mecanismo": 1 frase sobre o encaixe do mecanismo da oportunidade \
(subvenção/crédito/equity/bolsa) com o estágio e o momento da empresa.
- "recomendacao": "alta" | "media" | "baixa" — prioridade de leitura.

Regras: português; use SÓ a informação fornecida (você não conhece o edital \
além do subgrafo); na dúvida entre dois níveis de recomendação, o mais baixo."""

_RECOMENDACOES = frozenset({"alta", "media", "baixa"})

# Labels PT dos campos do perfil que o veredito lê (subset estruturado + textual;
# campos fora do mapa entram com o nome cru — melhor mostrar do que esconder).
_PROFILE_LABELS = {
    "nome": "Empresa",
    "tipo_entidade": "Tipo",
    "one_liner": "Proposta",
    "solution_summary": "Solução",
    "descricao_atividades": "Atividades",
    "tamanho_empresa": "Porte",
    "uf": "UF",
    "trl": "TRL",
    "estagio": "Estágio",
    "faturamento_anual": "Faturamento anual (R$)",
    "setor": "Setor",
}


def opportunity_node(graph: dict) -> dict | None:
    """Nó `Oportunidade(kind=edital)` de um subgrafo de edital — a unidade que o
    veredito julga no match de editais (matched_editais)."""
    for n in graph.get("nodes", []):
        if n.get("type") == "Oportunidade" and n.get("kind") == "edital":
            return n
    return None


def _ticket_label(tr: dict) -> str:
    """Formata `ticket_range` ({min_brl,max_brl}) em uma frase curta."""
    lo, hi = tr.get("min_brl"), tr.get("max_brl")
    fmt = lambda n: f"R$ {int(n):,}".replace(",", ".")  # noqa: E731
    if lo and hi:
        return f"{fmt(lo)} – {fmt(hi)}"
    if hi:
        return f"até {fmt(hi)}"
    return f"a partir de {fmt(lo)}" if lo else ""


def investment_offer_subgraph(graphs: dict, oportunidade_id: str) -> tuple[dict, dict] | None:
    """`(mini_graph, offer_node)` de uma oferta de investimento (PR8.1).

    A oferta coabita o arquivo `investidores` (multi-item) — não dá p/ serializar o
    arquivo inteiro (misturaria todos os fundos). Extrai o sub-grafo da oferta: o nó
    da oferta + o fundo (aresta `pertence_a`) + os Conceitos que o fundo viabiliza
    (aresta `viabiliza`) + essas arestas, e devolve um grafo enxuto que o
    `serialize_opportunity` consome como qualquer subgrafo de oportunidade."""
    from core.kg import hypergraph_catalog

    resolved = hypergraph_catalog.investment_offer(oportunidade_id, graphs)
    if resolved is None:
        return None
    offer, graph = resolved
    by_id = {n["id"]: n for n in graph.get("nodes", []) if n.get("id")}
    oid = offer.get("id")
    keep: set[str] = {oid}
    edges: list[dict] = []
    # 1. arestas que tocam a oferta (pertence_a → fundo).
    for e in graph.get("edges", []):
        if oid in e.get("members", []):
            edges.append(e)
            keep.update(e.get("members", []))
    fund_ids = {i for i in keep if (nd := by_id.get(i)) and nd.get("type") == "Ator"}
    # 2. arestas do fundo (viabiliza → Conceitos).
    for e in graph.get("edges", []):
        members = e.get("members", [])
        if fund_ids & set(members):
            if e not in edges:
                edges.append(e)
            keep.update(m for m in members if (nd := by_id.get(m)) and nd.get("type") == "Conceito")
    nodes = [by_id[i] for i in keep if i in by_id]
    return {"nodes": nodes, "edges": edges}, offer


def serialize_for_verdict(item: dict, graphs: dict) -> tuple[str, str] | None:
    """`(oportunidade_id, serialized)` de um item da fila, ou None. Dispatcher que
    unifica os DOIS formatos de oportunidade do funil (PR8.1):
      • edital       → subgrafo por file_key + nó kind=edital;
      • investimento → sub-grafo da oferta (offer+fundo+conceitos).
    O `oportunidade_id` é a chave do cache (file_key p/ editais, entity_id p/ ofertas)."""
    if item.get("kind") == "investimento":
        oid = str(item.get("oportunidade_id") or "")
        sub = investment_offer_subgraph(graphs, oid)
        if sub is None:
            return None
        mini, offer = sub
        return oid, serialize_opportunity(mini, offer)
    # default: edital (itens legados são {file_key, paths}, sem `kind`).
    fk = str(item.get("file_key") or item.get("oportunidade_id") or "")
    graph = graphs.get(fk)
    node = opportunity_node(graph) if graph else None
    if node is None:
        return None
    return fk, serialize_opportunity(graph, node)


def serialize_opportunity(graph: dict, node: dict) -> str:
    """Serializa o subgrafo da oportunidade em linguagem natural (D10/D12).

    Tudo que o juiz vê está aqui: propriedades categóricas (mecanismo, macro-temas,
    prazo/valor), constraints tipadas (renderizadas como frase, mesma função do
    card), texto residual de requisitos/exclusões, Conceitos cobertos e as ARESTAS
    nativas com rótulo dos membros (estilo HyperGraphRAG) — `exige`/`parceria_com`
    relacionais são exatamente o insumo das red flags."""
    from core.llm.agent_tools.explore_tools import _member_label, _node_index

    idx = _node_index(graph)
    kind = node.get("kind") or "oportunidade"
    aperture = node.get("aperture")
    lines = [f"OPORTUNIDADE [{kind}{f'/{aperture}' if aperture else ''}]: {node.get('name', '')}"]
    if node.get("description"):
        lines.append(f"descrição: {node['description']}")
    for field, label in (
        ("prazo", "prazo"), ("status", "status"), ("valor", "valor"),
        ("mecanismo", "mecanismo"), ("macro_temas", "macro-temas"),
        # Facetas de oferta de investimento (PR8.1) — só presentes em kind=investimento.
        ("estagio_alvo", "estágio-alvo"), ("lead_follow", "posição (lead/follow)"),
    ):
        v = node.get(field)
        if v:
            lines.append(f"{label}: {', '.join(v) if isinstance(v, list) else v}")
    tr = node.get("ticket_range")
    if isinstance(tr, dict) and (tr.get("min_brl") or tr.get("max_brl")):
        lines.append(f"ticket: {_ticket_label(tr)}")

    if node.get("constraints"):
        lines.append("constraints de elegibilidade (avaliadas no Estágio 0):")
        lines += [
            f"  • {_constraint_reason(c.get('tipo'), c.get('op'), c.get('valor'))}"
            for c in node["constraints"]
        ]
    for field, label in (
        ("requisitos_texto", "requisitos (texto residual)"),
        ("exclusoes_texto", "exclusões/vedações"),
    ):
        items = node.get(field) or []
        if items:
            lines.append(f"{label}:")
            lines += [f"  • {t}" for t in items[:_MAX_TEXT_ITEMS]]

    conceitos = [
        n.get("name", "") for n in graph.get("nodes", []) if n.get("type") == "Conceito"
    ]
    if conceitos:
        lines.append(f"conceitos cobertos: {', '.join(conceitos[:_MAX_CONCEITOS])}")

    edges = graph.get("edges", [])[:_MAX_EDGES]
    if edges:
        lines.append("relações do subgrafo:")
        for e in edges:
            members = ", ".join(_member_label(idx, m) for m in e.get("members", []))
            desc = (e.get("description") or "")[:120]
            lines.append(f"  • {e.get('type', '?')}: {members}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


def _profile_block(profile: Any) -> str:
    """Perfil (dict do CompanyProfileSchema) em linhas rotuladas, só campos
    preenchidos. Mesmo espírito do bloco de contexto do explore."""
    prof = profile if isinstance(profile, dict) else {}
    lines = []
    for field, label in _PROFILE_LABELS.items():
        v = prof.get(field)
        if v not in (None, "", [], "empresa"):
            lines.append(f"{label}: {v}")
    return "\n".join(lines) or "(perfil não informado)"


def _paths_block(paths: list[dict] | None) -> str:
    return "\n".join(
        f"  • «{p.get('src', '')}» (empresa) ↔ «{p.get('dst', '')}» (oportunidade), "
        f"cosseno {float(p.get('score', 0)):.2f}"
        for p in (paths or [])
    )


def verdict_input_hash(serialized: str, profile: Any, paths: list[dict] | None) -> str:
    """Chave de invalidação do cache: perfil, oportunidade (via serialização),
    paths do match ou versão do prompt mudaram ⇒ hash muda. Scores arredondados
    (3 casas) para o hash não flapar por ruído numérico de re-embedding."""
    prof = profile if isinstance(profile, dict) else {}
    payload = {
        "v": _PROMPT_VERSION,
        "subgraph": serialized,
        "profile": {k: v for k, v in sorted(prof.items()) if v not in (None, "", [])},
        "paths": [
            {"src": p.get("src"), "dst": p.get("dst"), "score": round(float(p.get("score", 0)), 3)}
            for p in (paths or [])
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _valid_verdict(v: Any) -> dict | None:
    """Coage/valida o output do LLM para o shape do card; inválido → None."""
    if not isinstance(v, dict):
        return None
    racional = str(v.get("racional_afinidade") or "").strip()
    reco = str(v.get("recomendacao") or "").strip().lower()
    if not racional or reco not in _RECOMENDACOES:
        return None
    flags = v.get("red_flags_elegibilidade")
    return {
        "racional_afinidade": racional,
        "red_flags_elegibilidade": [str(f).strip() for f in flags if str(f).strip()]
        if isinstance(flags, list) else [],
        "fit_mecanismo": str(v.get("fit_mecanismo") or "").strip(),
        "recomendacao": reco,
    }


def compute_verdict(
    serialized: str, profile: Any, paths: list[dict] | None = None,
    *, client=None, model: str | None = None,
) -> dict | None:
    """1 chamada tier 3 (JSON mode, temp 0) → veredito estruturado, ou None.

    Fail-open como os produtores de build (canonicalize/constraints): erro de
    infra/parse/validação NUNCA propaga — o card simplesmente fica sem veredito."""
    try:
        if client is None:
            from core.llm.llm_client import make_client
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY não definida (veredito usa o tier 3)")
            client = make_client(api_key=api_key)
        user = "\n\n".join(filter(None, [
            "[PERFIL DA EMPRESA]\n" + _profile_block(profile),
            ("[PARES DE AFINIDADE (Estágio 1)]\n" + _paths_block(paths)) if paths else "",
            "[SUBGRAFO DA OPORTUNIDADE]\n" + serialized,
        ]))
        resp = client.chat.completions.create(
            model=model or _verdict_model(),
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        verdict = _valid_verdict(json.loads(resp.choices[0].message.content))
        if verdict is None:
            logger.warning("compute_verdict: output fora do shape — descartado")
        return verdict
    except Exception as e:  # noqa: BLE001 — veredito nunca derruba match nem task
        logger.warning("compute_verdict: falha (%s) — sem veredito", e)
        return None


# ── cache (tabela match_verdicts, migration 035) ─────────────────────────────


def get_cached_verdicts(db, workspace_id: str, wanted: dict[str, str]) -> dict[str, dict]:
    """Hits do cache para `wanted` (oportunidade_id → input_hash esperado).
    Só devolve linhas cujo hash BATE — linha com hash velho é miss (perfil ou
    oportunidade mudou; a task vai recomputar e o upsert substitui)."""
    if not wanted:
        return {}
    rows = (
        db.table("match_verdicts")
        .select("oportunidade_id, input_hash, verdict")
        .eq("workspace_id", workspace_id)
        .in_("oportunidade_id", list(wanted))
        .execute()
        .data
        or []
    )
    return {
        r["oportunidade_id"]: r["verdict"]
        for r in rows
        if wanted.get(r["oportunidade_id"]) == r["input_hash"]
    }


def upsert_verdict(
    db, workspace_id: str, oportunidade_id: str, input_hash: str, verdict: dict, model: str,
) -> None:
    db.table("match_verdicts").upsert(
        {
            "workspace_id": workspace_id,
            "oportunidade_id": oportunidade_id,
            "input_hash": input_hash,
            "verdict": verdict,
            "model": model,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="workspace_id,oportunidade_id",
    ).execute()


# ── integração com o payload do match ────────────────────────────────────────


def attach_cached_verdicts(
    db, workspace_id: str, match_dicts: list[dict], profile: Any,
) -> list[dict]:
    """Anexa `verdict` (do cache; None = pendente) a cada match dict do payload
    e devolve os ITENS FALTANTES para a task computar:
    `[{"file_key", "paths"}]` — os misses são o que custa LLM (≤ K por refresh).

    `oportunidade_id` = file_key (`finep__602`) — a chave que o match e o
    frontend já usam; a coluna é TEXT e aceita node-id v2 quando o PR8 precisar."""
    from core.kg import kg_store

    graphs = kg_store.load_all_hypergraphs()
    wanted: dict[str, str] = {}
    items_by_oid: dict[str, dict] = {}
    for m in match_dicts:
        oid = f"{m.get('source', '')}__{m.get('edital_id', '')}"
        graph = graphs.get(oid)
        node = opportunity_node(graph) if graph else None
        if node is None:
            m["verdict"] = None
            continue
        serialized = serialize_opportunity(graph, node)
        paths = m.get("paths") or []
        wanted[oid] = verdict_input_hash(serialized, profile, paths)
        items_by_oid[oid] = {"file_key": oid, "paths": paths}

    hits = get_cached_verdicts(db, workspace_id, wanted)
    misses: list[dict] = []
    for m in match_dicts:
        oid = f"{m.get('source', '')}__{m.get('edital_id', '')}"
        if oid not in wanted:
            continue
        m["verdict"] = hits.get(oid)
        if oid not in hits:
            misses.append(items_by_oid[oid])
    return misses


def attach_cached_verdicts_entities(
    db, workspace_id: str, entity_dicts: list[dict], profile: Any,
) -> list[dict]:
    """Irmão de `attach_cached_verdicts` p/ as ofertas de INVESTIMENTO (PR8.1).

    Só `kind=investidor` (o fundo casa como Ator, mas o veredito é da sua OFERTA —
    D1). `oportunidade_id` = `entity_id` (`investidor:x`) — a chave que o card e a
    ficha já usam. Programa/ICT ficam sem veredito (fora do escopo do PR8.1)."""
    from core.kg import kg_store

    graphs = kg_store.load_all_hypergraphs()
    wanted: dict[str, str] = {}
    items_by_oid: dict[str, dict] = {}
    for ed in entity_dicts:
        if ed.get("kind") != "investidor":
            ed.setdefault("verdict", None)
            continue
        oid = ed.get("entity_id")
        sub = investment_offer_subgraph(graphs, oid) if oid else None
        if sub is None:
            ed["verdict"] = None
            continue
        mini, offer = sub
        serialized = serialize_opportunity(mini, offer)
        paths = ed.get("paths") or []
        wanted[oid] = verdict_input_hash(serialized, profile, paths)
        items_by_oid[oid] = {"kind": "investimento", "oportunidade_id": oid, "paths": paths}

    hits = get_cached_verdicts(db, workspace_id, wanted)
    misses: list[dict] = []
    for ed in entity_dicts:
        oid = ed.get("entity_id")
        if oid not in wanted:
            continue
        ed["verdict"] = hits.get(oid)
        if oid not in hits:
            misses.append(items_by_oid[oid])
    return misses


# Ordem de prioridade da recomendação; sem veredito = neutro (entre alta e baixa),
# para "alta" subir e "baixa" afundar sem punir os pendentes.
_RECO_RANK = {"alta": 0, "media": 1, "baixa": 2}


def reorder_by_verdict(match_dicts: list[dict]) -> list[dict]:
    """Reordena SÓ dentro do top-K recebido (D9): sort estável pela recomendação
    do veredito — empates (e pendentes) preservam a ordem de affinity de entrada."""
    return sorted(
        match_dicts,
        key=lambda m: _RECO_RANK.get((m.get("verdict") or {}).get("recomendacao"), 1),
    )
