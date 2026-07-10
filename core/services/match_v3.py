"""Match v3 — motor de produção do funil §7 (spec docs/specs/v3-unified.md).

SUBSTITUI o match por hipergrafo (hypergraph_match.py, linhagem hyper-extract,
DELETADO nesta fase). O match usa TEXTO REAL: chunks da empresa (company_chunks,
§5.4) × chunks do edital (match_chunks, embed contextualizado — gate 1.5) no
mesmo espaço de embedding. Sem LLM no ranking; parâmetros fixados pelo gate:

  Stage 0 — Vivo (determinístico): deadline MANDA quando presente (>= as_of
            passa; as_of parametrizável, default hoje); deadline NULL = fluxo
            contínuo PASSA e o status decide (NULL/aberta/ativa passam). O
            status congelado no ingest nunca mata um deadline futuro (mesma
            postura do entity_catalog: "deadline manda").
  Stage 1 — Elegibilidade (eligibility.py, camada única): unsat ELIMINA;
            unknown NUNCA elimina (perfil incompleto é o estado normal).
  Stage 2 — Afinidade: sum-of-max por chunk DA EMPRESA (família ColBERT;
            nunca max global), exposto como MÉDIA (0..1) p/ display/piso
            serem invariantes ao nº de chunks da empresa. boost_setores
            (×1.1 na interseção) ligado por default — gate: nunca piora.
  Stage 3 — Precisão (top-K): rerank opcional (MATCH_RERANK_ENABLED=true →
            core.reranker/RERANK_BACKEND, reordena SÓ o top-K) + veredito
            LLM async (match_verdict, anexado pelo router via cache/task).

Trilha INVESTIDOR (paralela): cosseno perfil-agregado × entities.embedding
(kind=investidor, fund_status ativo) + gate determinístico de estágio/setores
do metadata (só elimina quando os DOIS lados declaram e não casam — unknown
não elimina, mesma filosofia do Stage 1).

Explicabilidade: cada match carrega `matched_excerpts[]` — os pares (trecho da
empresa ↔ trecho do edital) que geraram o score, top-3. Mostrar o texto real
substitui os paths de conceito do v2 ("AI drafts, humans decide").

Dados via psycopg (DATABASE_URL) com snapshot em memória por processo,
revalidado por sonda barata (count+max(updated_at)) a cada chamada — recarrega
só quando o ingest mudou o corpus (~30MB p/ ~150 editais; mesmo papel do memo
de ecossistema do v2, sem disco).
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from core.services import eligibility
from core.services.company_chunks import (
    ensure_company_chunks,
    ephemeral_company_chunks,
    get_dsn,
    load_company_chunks,
    parse_vec,
)

logger = logging.getLogger(__name__)

# Piso do Stage 2 em escala de MÉDIA-dos-máximos (0..1, invariante ao nº de
# chunks da empresa). Calibrado no golden (matching.json, as_of 2026-07-05,
# corpus contextual 2026-07-10): positivos vivem em [0.548, 0.710], negativos
# median 0.540 — 0.52 corta 18/58 negativos do top-10 sem perder positivo
# (margem 0.028; 0.54 zeraria a margem). Abaixo do piso o item some do radar.
MIN_AFFINITY = float(os.getenv("MATCH_V3_MIN_AFFINITY", "0.52"))
# Piso da trilha investidor (cosseno único perfil×tese — teses vivem em
# cosseno mais baixo que o match de chunks).
MIN_INVESTOR_SCORE = float(os.getenv("MATCH_V3_MIN_INVESTOR", "0.30"))
BOOST_SETORES = 1.1
TOP_EXCERPTS = 3
_EXCERPT_CHARS = 280


# ===========================================================================
# Snapshot do corpus (memo por processo, revalidado por sonda)
# ===========================================================================

@dataclass
class _EntityChunks:
    texts: list[str]
    sections: list[list[str]]
    emb: np.ndarray  # (m, d) normalizado


@dataclass
class _Snapshot:
    probe: tuple
    opportunities: list[dict] = field(default_factory=list)   # kind edital|programa
    chunks: dict[str, _EntityChunks] = field(default_factory=dict)  # entity_id → chunks
    investors: list[dict] = field(default_factory=list)        # kind investidor (+_emb)


_SNAPSHOT: _Snapshot | None = None

_OPP_SQL = """
select id, kind, source, native_id, name, description, status, deadline, uf,
       setores, tecnologias_tags, ticket_min, ticket_max, constraints,
       requisitos_texto, metadata
from public.entities where kind in ('edital','programa')
"""

_INV_SQL = """
select id, native_id, name, description, status, setores, ticket_min, ticket_max,
       verificado_em, metadata, embedding
from public.entities where kind = 'investidor'
"""


def _probe(cur) -> tuple:
    cur.execute(
        "select count(*), coalesce(max(updated_at)::text, '') from public.entities"
    )
    a, b = cur.fetchone()
    cur.execute("select count(*) from public.match_chunks")
    (c,) = cur.fetchone()
    return (a, b, c)


def _normalize(m: np.ndarray) -> np.ndarray:
    if m.size == 0:
        return m
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


def _load_snapshot(conn) -> _Snapshot:
    with conn.cursor() as cur:
        probe = _probe(cur)
        cur.execute(_OPP_SQL)
        cols = [d.name for d in cur.description]
        opps = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        for o in opps:
            o["id"] = str(o["id"])

        cur.execute(
            "select entity_id, idx, section_path, text, embedding "
            "from public.match_chunks order by entity_id, idx"
        )
        raw: dict[str, list] = {}
        for eid, _idx, sp, text, emb in cur.fetchall():
            raw.setdefault(str(eid), []).append((sp or [], text, parse_vec(emb)))

        cur.execute(_INV_SQL)
        cols = [d.name for d in cur.description]
        invs = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        for v in invs:
            v["id"] = str(v["id"])
            e = parse_vec(v.pop("embedding")) if v.get("embedding") is not None else None
            v["_emb"] = (e / (np.linalg.norm(e) + 1e-9)) if e is not None and e.size else None

    chunks = {
        eid: _EntityChunks(
            texts=[t for _, t, _ in rows],
            sections=[sp for sp, _, _ in rows],
            emb=_normalize(np.stack([e for _, _, e in rows])),
        )
        for eid, rows in raw.items()
    }
    snap = _Snapshot(probe=probe, opportunities=opps, chunks=chunks, investors=invs)
    logger.info(
        "match_v3: snapshot carregado (%d oportunidades, %d entidades com chunks, %d investidores)",
        len(opps), len(chunks), len(invs),
    )
    return snap


def _get_snapshot() -> _Snapshot:
    """Sonda barata a cada chamada; recarrega só quando o ingest mudou."""
    global _SNAPSHOT
    import psycopg

    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        if _SNAPSHOT is not None:
            with conn.cursor() as cur:
                if _probe(cur) == _SNAPSHOT.probe:
                    return _SNAPSHOT
        _SNAPSHOT = _load_snapshot(conn)
    return _SNAPSHOT


def invalidate_snapshot() -> None:
    global _SNAPSHOT
    _SNAPSHOT = None


# ===========================================================================
# Stages 0-1 (determinísticos, testáveis sem DB)
# ===========================================================================

def stage0_alive(entity: dict, as_of: datetime.date) -> bool:
    """Vivo: deadline MANDA quando presente (staleness do status congelado no
    ingest nunca mata um prazo futuro); deadline NULL = fluxo contínuo, o
    status decide (NULL passa)."""
    if entity.get("deadline") is not None:
        return entity["deadline"] >= as_of
    return entity.get("status") is None or entity.get("status") in ("aberta", "ativa")


def stage1_eligibility(entity: dict, profile) -> dict | None:
    """Veredito de elegibilidade (None quando não há perfil = não filtra)."""
    if profile is None:
        return None
    return eligibility.evaluate_opportunity(entity.get("constraints"), profile)


# ===========================================================================
# Setores da empresa (heurística barata do gate — célula boost)
# ===========================================================================

def _deburr(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def infer_company_setores(profile) -> set[str]:
    """Setores da empresa por scan do texto do perfil contra a taxonomia
    fechada (mesma heurística validada na célula boost do bake-off 1.5)."""
    from core.kg import schema
    from core.services.eligibility import _profile_get

    text = _deburr(" ".join(str(_profile_get(profile, f) or "") for f in (
        "one_liner", "solution_summary", "descricao_atividades",
    ))).lower()
    labels = list(schema.setores_taxonomia().get("labels") or [])
    return {lbl for lbl in labels if _deburr(lbl).lower() in text}


# ===========================================================================
# Stage 2 — sum-of-max (exposto como média) + excerpts
# ===========================================================================

def _clip(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= _EXCERPT_CHARS else s[: _EXCERPT_CHARS - 1] + "…"


def score_entity(
    company_emb: np.ndarray, company_texts: list[str], ec: _EntityChunks,
) -> tuple[float, float, list[dict]]:
    """(affinity_mean, best_score, matched_excerpts) de UMA entidade.

    affinity = média dos máximos por chunk da empresa (sum-of-max ÷ n — mesma
    ORDENAÇÃO do sum-of-max do gate, escala 0..1 invariante ao nº de chunks).
    excerpts = os pares (chunk-empresa, melhor chunk-edital), top-3 por score,
    dedup pelo trecho do edital."""
    sims = company_emb @ ec.emb.T                     # (n_company, n_entity)
    best_j = sims.argmax(axis=1)                       # melhor chunk do edital p/ cada chunk-empresa
    best_v = sims[np.arange(sims.shape[0]), best_j]
    affinity = float(best_v.mean())
    order = np.argsort(-best_v)
    excerpts: list[dict] = []
    seen: set[int] = set()
    for i in order:
        j = int(best_j[i])
        if j in seen:
            continue
        seen.add(j)
        section = ec.sections[j]
        excerpts.append({
            "company_text": _clip(company_texts[int(i)]),
            "edital_text": _clip(ec.texts[j]),
            "section": section[-1] if section else None,
            "score": round(float(best_v[i]), 3),
        })
        if len(excerpts) >= TOP_EXCERPTS:
            break
    return affinity, float(best_v.max()), excerpts


# ===========================================================================
# Payloads
# ===========================================================================

@dataclass
class OpportunityMatch:
    """Edital/programa que casa com a empresa — payload do card do radar."""

    kind: str               # edital | programa
    source: str
    edital_id: str          # parte local do native_id (ex. "589") — contrato do front
    entity_id: str          # native_id completo (ex. "finep:589" / "programa:centelha")
    name: str
    description: str
    score: float            # melhor par (cosseno 0..1) — display do ring
    affinity: float         # média dos máximos (0..1, com boost) — chave de RANKING
    setores: list[str]
    matched_excerpts: list[dict]
    status: str | None
    prazo: str | None       # dd/mm/yyyy (display)
    valor: str | None
    url: str | None
    elegibilidade: dict | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "source": self.source,
            "edital_id": self.edital_id,
            "entity_id": self.entity_id,
            "name": self.name,
            "description": self.description,
            "score": round(self.score, 3),
            "affinity": round(self.affinity, 3),
            "setores": self.setores,
            "matched_excerpts": self.matched_excerpts,
            "status": self.status,
            "prazo": self.prazo,
            "valor": self.valor,
            "url": self.url,
            "elegibilidade": self.elegibilidade,
        }


@dataclass
class InvestorMatch:
    """Investidor cuja tese casa com o perfil (trilha paralela)."""

    entity_id: str          # native_id ("investidor:indicator-capital")
    name: str
    description: str        # tese
    score: float            # cosseno perfil-agregado × tese
    setores: list[str]
    estagio_alvo: list[str]
    matched_excerpts: list[dict]
    offer: dict | None      # facetas do card (site/ticket/estágio)
    verificado: bool

    def to_dict(self) -> dict:
        return {
            "kind": "investidor",
            "entity_id": self.entity_id,
            "name": self.name,
            "description": self.description,
            "score": round(self.score, 3),
            "affinity": round(self.score, 3),  # mesma escala 0..1 do funil (ranking unificado)
            "setores": self.setores,
            "estagio_alvo": self.estagio_alvo,
            "matched_excerpts": self.matched_excerpts,
            "offer": self.offer,
            "verificado": self.verificado,
        }


def _valor_display(e: dict) -> str | None:
    tmin, tmax = e.get("ticket_min"), e.get("ticket_max")
    if tmin is None and tmax is None:
        return None
    if tmin is not None and tmax is not None and tmin != tmax:
        return f"R$ {tmin:,.0f} – R$ {tmax:,.0f}"
    return f"R$ {(tmin if tmin is not None else tmax):,.0f}"


def _prazo_display(e: dict) -> str | None:
    d = e.get("deadline")
    return d.strftime("%d/%m/%Y") if d else None


def _split_native(native_id: str) -> tuple[str, str]:
    src, _, local = (native_id or "").partition(":")
    return (src, local) if local else ("", native_id)


# ===========================================================================
# Lado empresa
# ===========================================================================

def _as_profile_dict(profile) -> dict | None:
    if profile is None:
        return None
    if hasattr(profile, "model_dump"):      # pydantic (CompanyProfileSchema)
        return profile.model_dump()
    return profile  # dict ou CompanyProfile (eligibility/_profile_get aceitam ambos)


def _company_side(
    profile, *, workspace_id: str | None, db=None, use_hyde: bool = True,
) -> tuple[list[str], np.ndarray]:
    """(texts, emb normalizado). Workspace autenticado → company_chunks
    (refresh on-demand); anônimo/eval → efêmero do perfil."""
    if workspace_id:
        try:
            ensure_company_chunks(workspace_id, profile, db=db)
            texts, embs = load_company_chunks(workspace_id)
            if len(texts):
                return texts, _normalize(embs)
        except Exception as e:  # noqa: BLE001 — cai no efêmero, não derruba o match
            logger.warning("match_v3: company_chunks falhou (%s) — caminho efêmero", e)
    texts, embs = ephemeral_company_chunks(profile, use_hyde=use_hyde)
    return texts, _normalize(embs)


# ===========================================================================
# Funil (Stage 0 → 1 → 2 → 3)
# ===========================================================================

def find_matching_opportunities(
    profile,
    *,
    workspace_id: str | None = None,
    db=None,
    kinds: frozenset | set = frozenset({"edital", "programa"}),
    as_of: datetime.date | None = None,
    top_k: int = 8,
    min_affinity: float | None = None,
    boost: bool = True,
    use_hyde: bool = True,
    rerank: bool | None = None,
) -> list[OpportunityMatch]:
    """Funil completo §7 sobre editais/programas. `profile` (dict, pydantic ou
    CompanyProfile) alimenta o lado empresa (chunks) E o Stage 1 (constraints).

    `as_of` parametriza o Stage 0 (default: hoje). `min_affinity` sobrescreve o
    piso (0 = sem piso — usado pela eval p/ medir o ranking completo).
    `rerank` força/desliga o Stage 3 (default: env MATCH_RERANK_ENABLED)."""
    as_of = as_of or datetime.date.today()
    floor = MIN_AFFINITY if min_affinity is None else min_affinity
    prof = _as_profile_dict(profile)

    company_texts, company_emb = _company_side(
        prof, workspace_id=workspace_id, db=db, use_hyde=use_hyde,
    )
    if not company_texts:
        logger.info("match_v3: perfil sem texto — nenhum match")
        return []

    snap = _get_snapshot()
    company_setores = infer_company_setores(prof) if boost else set()

    matches: list[OpportunityMatch] = []
    n_stage0 = n_stage1 = 0
    for e in snap.opportunities:
        if e["kind"] not in kinds:
            continue
        if not stage0_alive(e, as_of):
            n_stage0 += 1
            continue
        eleg = stage1_eligibility(e, prof)
        if eleg is not None and eleg["status"] == eligibility.INELEGIVEL:
            n_stage1 += 1
            continue
        ec = snap.chunks.get(e["id"])
        if ec is None or ec.emb.size == 0:
            continue
        affinity, best, excerpts = score_entity(company_emb, company_texts, ec)
        if boost and company_setores & set(e.get("setores") or []):
            affinity *= BOOST_SETORES
        if affinity < floor:
            continue
        src, local = _split_native(e["native_id"])
        matches.append(OpportunityMatch(
            kind=e["kind"], source=src or e.get("source", ""), edital_id=local,
            entity_id=e["native_id"], name=e.get("name") or "",
            description=(e.get("description") or "")[:240],
            score=best, affinity=affinity,
            setores=list(e.get("setores") or []), matched_excerpts=excerpts,
            status=("aberta" if e.get("deadline") else e.get("status")),
            prazo=_prazo_display(e), valor=_valor_display(e),
            url=(e.get("metadata") or {}).get("url"),
            elegibilidade=eleg,
        ))
    matches.sort(key=lambda m: m.affinity, reverse=True)
    matches = matches[:top_k]

    do_rerank = _rerank_enabled() if rerank is None else rerank
    if do_rerank:
        matches = _stage3_rerank(prof, matches)

    logger.info(
        "match_v3: %d matches (as_of=%s, stage0_mortos=%d, stage1_unsat=%d, piso=%.2f)",
        len(matches), as_of, n_stage0, n_stage1, floor,
    )
    return matches


def _rerank_enabled() -> bool:
    return os.getenv("MATCH_RERANK_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _stage3_rerank(prof, matches: list[OpportunityMatch]) -> list[OpportunityMatch]:
    """Reordena SÓ o top-K via core.reranker (RERANK_BACKEND). Falha/indisponível
    → mantém a ordem geométrica (contrato do rerank_scores)."""
    from core.reranker import rerank_scores
    from core.services.eligibility import _profile_get

    query = " ".join(str(_profile_get(prof, f) or "") for f in (
        "one_liner", "solution_summary", "descricao_atividades",
    )).strip()
    if not query or not matches:
        return matches
    texts = [
        f"{m.name}. " + " ".join(x["edital_text"] for x in m.matched_excerpts)
        for m in matches
    ]
    scores = rerank_scores(query, texts)
    if scores is None:
        return matches
    order = sorted(range(len(matches)), key=lambda i: scores[i], reverse=True)
    return [matches[i] for i in order]


# ===========================================================================
# Trilha investidor
# ===========================================================================

def find_matching_investors(
    profile,
    *,
    workspace_id: str | None = None,
    db=None,
    top_k: int = 5,
    min_score: float | None = None,
    use_hyde: bool = True,
) -> list[InvestorMatch]:
    """Cosseno perfil-agregado × tese (entities.embedding, kind=investidor,
    fund_status ativo) + gates determinísticos de metadata: estágio e setores
    só eliminam quando os DOIS lados declaram e não casam (unknown não
    elimina). Investidor generalista/Multissetorial nunca é gateado por setor."""
    floor = MIN_INVESTOR_SCORE if min_score is None else min_score
    prof = _as_profile_dict(profile)

    company_texts, company_emb = _company_side(
        prof, workspace_id=workspace_id, db=db, use_hyde=use_hyde,
    )
    if not company_texts:
        return []
    agg = company_emb.mean(axis=0)
    agg = agg / (np.linalg.norm(agg) + 1e-9)

    from core.services.eligibility import _profile_get
    estagio = str(_profile_get(prof, "estagio") or "").strip().lower()
    company_setores = infer_company_setores(prof)

    snap = _get_snapshot()
    out: list[InvestorMatch] = []
    for v in snap.investors:
        meta = v.get("metadata") or {}
        if (meta.get("fund_status") or "").lower() != "ativo":
            continue
        if v.get("_emb") is None:
            continue
        # Gate de estágio: elimina só quando perfil E tese declaram e não casam.
        alvo = [str(s).lower() for s in (meta.get("estagio_alvo") or [])]
        if estagio and alvo and estagio not in alvo:
            continue
        # Gate de setores: só p/ investidor NÃO-generalista com setor declarado.
        inv_setores = set(v.get("setores") or [])
        setor_declarado = inv_setores and inv_setores != {"Multissetorial"}
        if (not meta.get("generalista")) and setor_declarado and company_setores \
                and not (company_setores & inv_setores):
            continue
        score = float(agg @ v["_emb"])
        if score < floor:
            continue
        # Excerpt: melhor chunk da empresa contra a tese (justificativa do card).
        sims = company_emb @ v["_emb"]
        bi = int(np.argmax(sims))
        out.append(InvestorMatch(
            entity_id=v["native_id"], name=v.get("name") or "",
            description=v.get("description") or "", score=score,
            setores=sorted(inv_setores), estagio_alvo=list(meta.get("estagio_alvo") or []),
            matched_excerpts=[{
                "company_text": _clip(company_texts[bi]),
                "edital_text": _clip(v.get("description") or ""),
                "section": "tese",
                "score": round(float(sims[bi]), 3),
            }],
            offer={
                "offer_name": v.get("name") or "",
                "official_url": meta.get("site") or "",
                "estagio_alvo": list(meta.get("estagio_alvo") or []),
                "ticket_range": (
                    {"min_brl": v.get("ticket_min"), "max_brl": v.get("ticket_max")}
                    if v.get("ticket_min") is not None or v.get("ticket_max") is not None
                    else None
                ),
            },
            verificado=v.get("verificado_em") is not None,
        ))
    out.sort(key=lambda m: m.score, reverse=True)
    logger.info("match_v3: %d investidores (piso=%.2f)", len(out[:top_k]), floor)
    return out[:top_k]


# ===========================================================================
# Helpers p/ eval / veredito
# ===========================================================================

def get_opportunity(native_id: str) -> dict | None:
    """Linha de `entities` (snapshot) por native_id — insumo do veredito/eval."""
    snap = _get_snapshot()
    return next((e for e in snap.opportunities if e["native_id"] == native_id), None)


def stage1_verdict(native_id: str, profile) -> dict | None:
    """Veredito de elegibilidade de UMA oportunidade (hard negatives da eval)."""
    e = get_opportunity(native_id)
    if e is None:
        return None
    return eligibility.evaluate_opportunity(e.get("constraints"), _as_profile_dict(profile))
