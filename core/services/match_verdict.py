"""Veredito LLM top-K — Estágio 3 (precisão) do funil de match v3.

O match core segue SEM LLM no ranking: o LLM entra só aqui, DEPOIS do filtro
vivo (Stage 0), da elegibilidade (Stage 1) e da afinidade sum-of-max (Stage 2),
para produzir um veredito ESTRUTURADO sobre cada par (empresa, oportunidade) do
top-K — razões, não score:

    { racional_afinidade, red_flags_elegibilidade[], fit_mecanismo, recomendacao }

Input do juiz (Fase 2 — v3): a FICHA da oportunidade serializada da linha de
`entities` (constraints tipadas, requisitos_texto residual, ticket, prazo,
mecanismo, setores) + os `matched_excerpts` (pares de trechos reais empresa ↔
edital que geraram o score no Stage 2). Nada de subgrafo — o hipergrafo morreu
com o match v2.

Custo escala com K, não com o corpus: a chamada é async (task procrastinate
`compute_match_verdicts`) e cacheada por par em `match_verdicts` (migration 035),
com invalidação implícita por `input_hash` — perfil, oportunidade ou prompt
mudou ⇒ hash muda ⇒ recomputa e o upsert substitui. O card renderiza sem o
veredito e o recebe quando pronto (poll cache-only, zero LLM).

Chave do cache (`oportunidade_id`): editais mantêm o formato do front
(`{source}__{edital_id}`, ex. `finep__589`); programas/investidores usam o
native_id (`programa:centelha`, `investidor:kptl`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from core.services.eligibility import _reason as _constraint_reason
from core.services.eligibility import format_curated_rules_block

logger = logging.getLogger(__name__)

# Modelo do veredito = tier 3 já em produção (OPENAI_MODEL), overridável em
# separado (VERDICT_MODEL) sem mexer no tier — mesma postura do CONSTRAINTS_MODEL.
def _verdict_model() -> str:
    return os.getenv("VERDICT_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# Versão do prompt — entra no input_hash: mudar o prompt invalida o cache inteiro.
# v3 = input vira ficha de entities + matched_excerpts (Fase 2 do v3-unified).
_PROMPT_VERSION = "v3"

_MAX_TEXT_ITEMS = 12
_MAX_EXCERPTS = 5

_SYSTEM = """Você é o veredito final do radar de oportunidades de fomento à \
inovação (estágio de precisão do funil de match). Recebe o PERFIL de uma \
empresa, a FICHA de uma oportunidade (edital/programa/tese de investimento) já \
pré-selecionada por afinidade semântica e filtro de elegibilidade, e os TRECHOS \
reais (empresa ↔ oportunidade) que geraram o match. Seu papel é EXPLICAR o fit \
para o usuário decidir — você produz razões, não pontuação.

Responda JSON com EXATAMENTE estas chaves:
- "racional_afinidade": 2-3 frases concretas sobre POR QUE o conteúdo da \
oportunidade casa (ou não) com o que a empresa faz — ancore nos trechos \
fornecidos e diga também o que NÃO conecta.
- "red_flags_elegibilidade": lista (pode ser vazia) de alertas OBJETIVOS \
extraídos dos requisitos e constraints da ficha — ex. exige parceria com ICT, \
restrição de porte/faturamento/UF, contrapartida. Frases curtas. NÃO invente: \
só o que está na ficha.
- "fit_mecanismo": 1 frase sobre o encaixe do mecanismo da oportunidade \
(subvenção/bolsa/parceria/equity) com o estágio e o momento da empresa.
- "recomendacao": "alta" | "media" | "baixa" — prioridade de leitura.

Regras: português; use SÓ a informação fornecida (você não conhece o edital \
além da ficha); na dúvida entre dois níveis de recomendação, o mais baixo."""

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


def _fmt_brl(n) -> str:
    return f"R$ {int(n):,}".replace(",", ".")


def serialize_entity(row: dict) -> str:
    """Ficha da oportunidade em linguagem natural, a partir da linha de
    `entities` (dict com as colunas do gold — snapshot do match_v3 ou SELECT).
    Tudo que o juiz vê está aqui: mecanismo/formato/prazo/ticket, constraints
    tipadas (renderizadas como frase, mesma função do card), requisitos
    residuais, setores e tags."""
    kind = row.get("kind") or "oportunidade"
    lines = [f"OPORTUNIDADE [{kind}]: {row.get('name', '')}"]
    if row.get("description"):
        lines.append(f"descrição: {row['description']}")

    meta = row.get("metadata") or {}
    deadline = row.get("deadline")
    prazo = deadline.strftime("%d/%m/%Y") if hasattr(deadline, "strftime") else (deadline or None)
    simple = (
        ("mecanismo", row.get("mecanismo")),
        ("formato", row.get("formato")),
        ("status", row.get("status")),
        ("prazo", prazo),
        ("UF", row.get("uf")),
        ("setores", ", ".join(row.get("setores") or []) or None),
        ("tecnologias", ", ".join(row.get("tecnologias_tags") or []) or None),
        ("estágio-alvo", ", ".join(meta.get("estagio_alvo") or []) or None),
        ("posição (lead/follow)", meta.get("lead_follow")),
    )
    lines += [f"{label}: {v}" for label, v in simple if v]

    tmin, tmax = row.get("ticket_min"), row.get("ticket_max")
    if tmin and tmax:
        lines.append(f"ticket: {_fmt_brl(tmin)} – {_fmt_brl(tmax)}")
    elif tmax:
        lines.append(f"ticket: até {_fmt_brl(tmax)}")
    elif tmin:
        lines.append(f"ticket: a partir de {_fmt_brl(tmin)}")

    if row.get("constraints"):
        lines.append("constraints de elegibilidade (avaliadas no Stage 1):")
        lines += [
            f"  • {_constraint_reason(c.get('tipo'), c.get('op'), c.get('valor'))}"
            for c in row["constraints"]
        ]
    reqs = row.get("requisitos_texto") or []
    if reqs:
        lines.append("requisitos (texto residual):")
        lines += [f"  • {t}" for t in reqs[:_MAX_TEXT_ITEMS]]
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


def _excerpts_block(excerpts: list[dict] | None) -> str:
    return "\n".join(
        f"  • empresa: «{x.get('company_text', '')}»\n"
        f"    oportunidade: «{x.get('edital_text', '')}» (cosseno {float(x.get('score', 0)):.2f})"
        for x in (excerpts or [])[:_MAX_EXCERPTS]
    )


def verdict_input_hash(serialized: str, profile: Any, excerpts: list[dict] | None) -> str:
    """Chave de invalidação do cache: perfil, oportunidade (via serialização),
    trechos do match ou versão do prompt mudaram ⇒ hash muda. Scores arredondados
    (3 casas) para o hash não flapar por ruído numérico de re-embedding."""
    prof = profile if isinstance(profile, dict) else {}
    payload = {
        "v": _PROMPT_VERSION,
        "ficha": serialized,
        "profile": {k: v for k, v in sorted(prof.items()) if v not in (None, "", [])},
        "excerpts": [
            {
                "company": x.get("company_text"),
                "edital": x.get("edital_text"),
                "score": round(float(x.get("score", 0)), 3),
            }
            for x in (excerpts or [])
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
    serialized: str, profile: Any, excerpts: list[dict] | None = None,
    *, client=None, model: str | None = None,
) -> dict | None:
    """1 chamada tier 3 (JSON mode, temp 0) → veredito estruturado, ou None.

    Fail-open como os produtores de build: erro de infra/parse/validação NUNCA
    propaga — o card simplesmente fica sem veredito."""
    try:
        if client is None:
            from core.llm.llm_client import make_client
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY não definida (veredito usa o tier 3)")
            client = make_client(api_key=api_key)
        rules_block = format_curated_rules_block(profile)
        user = "\n\n".join(filter(None, [
            "[PERFIL DA EMPRESA]\n" + _profile_block(profile),
            ("[TRECHOS QUE GERARAM O MATCH (Stage 2)]\n" + _excerpts_block(excerpts))
            if excerpts else "",
            ("[REGRAS DE ELEGIBILIDADE (TABELAS CURADAS)]\n" + rules_block) if rules_block else "",
            "[FICHA DA OPORTUNIDADE]\n" + serialized,
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


# ── integração com o payload do match v3 ─────────────────────────────────────


def verdict_key(m: dict) -> str | None:
    """`oportunidade_id` de um match dict v3: editais mantêm `{source}__{id}`
    (formato que o front já usa no poll); programas/investidores usam o
    native_id (`entity_id`)."""
    if m.get("kind") == "edital":
        if m.get("source") and m.get("edital_id"):
            return f"{m['source']}__{m['edital_id']}"
        return None
    return m.get("entity_id") or None


def _entity_row(oid: str) -> dict | None:
    """Linha de `entities` p/ um oportunidade_id (edital `src__id` ou native_id)."""
    from core.services import match_v3

    native = oid.replace("__", ":", 1) if "__" in oid else oid
    row = match_v3.get_opportunity(native)
    if row is not None:
        return row
    # investidor: vive na trilha paralela do snapshot
    snap = match_v3._get_snapshot()
    return next((v for v in snap.investors if v["native_id"] == native), None)


def serialize_for_verdict(item: dict) -> tuple[str, str] | None:
    """`(oportunidade_id, ficha_serializada)` de um item da fila
    (`{oportunidade_id, excerpts}`), ou None se a entidade sumiu do corpus."""
    oid = str(item.get("oportunidade_id") or "")
    if not oid:
        return None
    row = _entity_row(oid)
    if row is None:
        return None
    return oid, serialize_entity(row)


def attach_cached_verdicts(
    db, workspace_id: str, match_dicts: list[dict], profile: Any,
) -> list[dict]:
    """Anexa `verdict` (do cache; None = pendente) a cada match dict v3 (edital,
    programa OU investidor — chave via `verdict_key`) e devolve os ITENS
    FALTANTES para a task computar: `[{oportunidade_id, excerpts}]` — os misses
    são o que custa LLM (≤ K por refresh)."""
    wanted: dict[str, str] = {}
    items_by_oid: dict[str, dict] = {}
    for m in match_dicts:
        oid = verdict_key(m)
        row = _entity_row(oid) if oid else None
        if row is None:
            m.setdefault("verdict", None)
            continue
        serialized = serialize_entity(row)
        excerpts = m.get("matched_excerpts") or []
        wanted[oid] = verdict_input_hash(serialized, profile, excerpts)
        items_by_oid[oid] = {"oportunidade_id": oid, "excerpts": excerpts}

    hits = get_cached_verdicts(db, workspace_id, wanted)
    misses: list[dict] = []
    for m in match_dicts:
        oid = verdict_key(m)
        if oid not in wanted:
            continue
        m["verdict"] = hits.get(oid)
        if oid not in hits:
            misses.append(items_by_oid[oid])
    return misses


# Ordem de prioridade da recomendação; sem veredito = neutro (entre alta e baixa),
# para "alta" subir e "baixa" afundar sem punir os pendentes.
_RECO_RANK = {"alta": 0, "media": 1, "baixa": 2}


def reorder_by_verdict(match_dicts: list[dict]) -> list[dict]:
    """Reordena SÓ dentro do top-K recebido: sort estável pela recomendação
    do veredito — empates (e pendentes) preservam a ordem de affinity de entrada."""
    return sorted(
        match_dicts,
        key=lambda m: _RECO_RANK.get((m.get("verdict") or {}).get("recomendacao"), 1),
    )
