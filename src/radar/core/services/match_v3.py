"""Match v3 — motor de produção do funil §7 (spec docs/specs/v3-unified.md).

SUBSTITUI o match por hipergrafo (hypergraph_match.py, linhagem hyper-extract,
DELETADO nesta fase). O match usa TEXTO REAL: chunks da empresa (company_chunks,
§5.4) × chunks do edital (match_chunks, embed contextualizado — gate 1.5) no
mesmo espaço de embedding. Sem LLM no ranking; parâmetros fixados pelo gate:

  Stage 0 — Vivo (determinístico): usa exclusivamente o read model temporal
            canônico. Apenas ``active`` passa; ``closed`` e ``needs_review``
            ficam fora do match ativo.
  Stage 1 — Elegibilidade (eligibility.py, camada única): unsat ELIMINA;
            unknown NUNCA elimina (perfil incompleto é o estado normal).
  Stage 2 — Afinidade: sum-of-max por chunk DA EMPRESA (família ColBERT;
            nunca max global), exposto como MÉDIA (0..1) p/ display/piso
            serem invariantes ao nº de chunks da empresa. boost_setores
            (×1.1 na interseção) ligado por default — gate: nunca piora.
  Stage 3 — Precisão (top-K): rerank opcional (MATCH_RERANK_ENABLED=true →
            radar.core.reranker/RERANK_BACKEND, reordena SÓ o top-K) + veredito
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
from zoneinfo import ZoneInfo

import numpy as np

from radar.core.services import domain_paths, eligibility
from radar.core.services.company_chunks import (
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
select id, kind, source, native_id, name, description, status, deadline, uf, updated_at,
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

        # Trilha investidor DESATIVADA (spec product-scope-catalog-deactivation.md):
        # os dados históricos permanecem em `entities` (kind=investidor), mas o
        # snapshot do match não alimenta mais a trilha paralela de investidores.
        invs: list[dict] = []

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


# ===========================================================================
# Stages 0-1 (determinísticos, testáveis sem DB)
# ===========================================================================

def stage0_alive(temporal) -> bool:
    """Stage 0 só aceita a projeção temporal canônica ``active``."""
    from radar.domain.data_quality import ValidityState

    return temporal.validity_state is ValidityState.ACTIVE


def _today_sao_paulo() -> datetime.date:
    """Dia civil canônico do Stage 0; ``as_of`` explícito sempre prevalece."""
    return datetime.datetime.now(ZoneInfo("America/Sao_Paulo")).date()


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
    from radar.core.kg import schema
    from radar.core.services.eligibility import _profile_get

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
    company_origins: list[str] | None = None,
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
    origins = company_origins if company_origins is not None else []
    for i in order:
        j = int(best_j[i])
        if j in seen:
            continue
        origin = origins[int(i)] if int(i) < len(origins) else "profile"
        if origin not in {"profile", "library_doc"}:
            continue
        seen.add(j)
        section = ec.sections[j]
        excerpts.append({
            "company_text": _clip(company_texts[int(i)]),
            "edital_text": _clip(ec.texts[j]),
            "section": section[-1] if section else None,
            "score": round(float(best_v[i]), 3),
            "origin": origin,
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
    score: float            # métrica canônica: afinidade de escopo (0..1)
    affinity: float         # média dos máximos (0..1, com boost) — chave de RANKING
    setores: list[str]
    matched_excerpts: list[dict]
    status: str | None
    prazo: str | None       # dd/mm/yyyy (display)
    valor: str | None
    url: str | None
    elegibilidade: dict | None = None
    temporal_mode: str | None = None
    validity_state: str | None = None
    temporal_value: str | None = None
    decision_source: str | None = None
    last_verified_at: str | None = None
    technical_score: float | None = None  # melhor par; detalhe técnico, não ranking
    # Caminho de inovação (spec product-pathways-domain-matching.md) — anotação
    # ADITIVA: não entra no ranking. `tipo` nunca é "investidor".
    tipo: str | None = None
    caminho: dict | None = None
    explicacao: dict | None = None

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
            "technical_score": round(self.technical_score, 3) if self.technical_score is not None else None,
            "setores": self.setores,
            "matched_excerpts": self.matched_excerpts,
            "status": self.status,
            "prazo": self.prazo,
            "valor": self.valor,
            "url": self.url,
            "elegibilidade": self.elegibilidade,
            "temporal_mode": self.temporal_mode,
            "validity_state": self.validity_state,
            "temporal_value": self.temporal_value,
            "decision_source": self.decision_source,
            "last_verified_at": self.last_verified_at,
            "tipo": self.tipo,
            "caminho": self.caminho,
            "explicacao": self.explicacao,
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


def _prazo_display(temporal) -> str | None:
    if temporal.temporal_mode.value != "fixed" or not temporal.temporal_value:
        return None
    try:
        return datetime.date.fromisoformat(temporal.temporal_value).strftime("%d/%m/%Y")
    except ValueError:
        return None


def _split_native(native_id: str) -> tuple[str, str]:
    src, _, local = (native_id or "").partition(":")
    return (src, local) if local else ("", native_id)


def _node_id(kind: str, native_id: str) -> str:
    """Id de nó do spike: `<kind>:<native_id>` (ex.: `edital:finep:589`)."""
    return f"{kind}:{native_id}"


def _status_from_temporal(temporal) -> str:
    from radar.domain.data_quality import ValidityState

    if temporal.validity_state is ValidityState.ACTIVE:
        return "aberta"
    if temporal.validity_state is ValidityState.CLOSED:
        return "encerrada"
    return "desconhecido"


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
) -> tuple[list[str], np.ndarray, list[str]]:
    """(texts, emb normalizado). Workspace autenticado → company_chunks
    (refresh on-demand); anônimo/eval → efêmero do perfil."""
    if workspace_id:
        try:
            ensure_company_chunks(workspace_id, profile, db=db)
            texts, embs, origins = load_company_chunks(workspace_id, include_origins=True)
            if len(texts):
                return texts, _normalize(embs), origins
        except Exception as e:  # noqa: BLE001 — cai no efêmero, não derruba o match
            logger.warning("match_v3: company_chunks falhou (%s) — caminho efêmero", e)
    texts, embs, origins = ephemeral_company_chunks(profile, use_hyde=use_hyde)
    return texts, _normalize(embs), origins


def prepare_company_side(profile, *, workspace_id: str | None, db=None, use_hyde: bool = True):
    return _company_side(profile, workspace_id=workspace_id, db=db, use_hyde=use_hyde)


def _unpack_company_side(side):
    if len(side) == 3:
        return side
    texts, embs = side
    return texts, embs, ["profile"] * len(texts)


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
    prepared_company=None,
    structural_boost: bool = False,
    structural_alpha: float | None = None,
) -> list[OpportunityMatch]:
    """Funil completo §7 sobre editais/programas. `profile` (dict, pydantic ou
    CompanyProfile) alimenta o lado empresa (chunks) E o Stage 1 (constraints).

    `as_of` parametriza o Stage 0 (default: hoje). `min_affinity` sobrescreve o
    piso (0 = sem piso — usado pela eval p/ medir o ranking completo).
    `rerank` força/desliga o Stage 3 (default: env MATCH_RERANK_ENABLED).
    `structural_boost` (default off) aplica o boost de vizinhança `similar_a`
    do kg_spike no Stage 2 (célula A/B de avaliação — nunca muda produção)."""
    as_of = as_of or _today_sao_paulo()
    floor = MIN_AFFINITY if min_affinity is None else min_affinity
    prof = _as_profile_dict(profile)

    company_texts, company_emb, company_origins = _unpack_company_side(
        prepared_company or _company_side(prof, workspace_id=workspace_id, db=db, use_hyde=use_hyde)
    )
    if not company_texts:
        logger.info("match_v3: perfil sem texto — nenhum match")
        return []

    snap = _get_snapshot()
    from radar.core.services.temporal_read_model import (
        resolve_temporal_read_models,
        subjects_from_rows,
    )

    temporal_by_id = resolve_temporal_read_models(
        subjects_from_rows(snap.opportunities), as_of=as_of,
    )
    company_setores = infer_company_setores(prof) if boost else set()
    # Temas do perfil p/ anotação de caminho (independente do boost de ranking).
    company_themes = infer_company_setores(prof)
    has_project = domain_paths.has_project(prof)

    matches: list[OpportunityMatch] = []
    n_stage0 = n_stage1 = 0
    scored: list[dict] = []
    for e in snap.opportunities:
        if e["kind"] not in kinds:
            continue
        temporal = temporal_by_id.get(e["native_id"])
        if temporal is None or not stage0_alive(temporal):
            n_stage0 += 1
            continue
        eleg = stage1_eligibility(e, prof)
        if eleg is not None and eleg["status"] == eligibility.INELEGIVEL:
            n_stage1 += 1
            continue
        ec = snap.chunks.get(e["id"])
        if ec is None or ec.emb.size == 0:
            continue
        affinity, best, excerpts = score_entity(company_emb, company_texts, ec, company_origins)
        if boost and company_setores & set(e.get("setores") or []):
            affinity *= BOOST_SETORES
        scored.append({
            "e": e, "temporal": temporal, "eleg": eleg,
            "affinity": affinity, "best": best, "excerpts": excerpts,
        })

    if structural_boost:
        from radar.core.kg.spike import match_boost
        # Seeds = match que já passariam o piso de produção (MIN_AFFINITY), não
        # o piso da eval — a célula A/B mede o ranking completo com min_affinity=0.
        seeds = {
            _node_id(s["e"]["kind"], s["e"]["native_id"])
            for s in scored if s["affinity"] >= MIN_AFFINITY
        }
        if seeds:
            factors = match_boost.structural_factors(
                seeds, alpha=structural_alpha,
            )
            for s in scored:
                s["affinity"] *= factors.get(
                    _node_id(s["e"]["kind"], s["e"]["native_id"]), 1.0,
                )

    for s in scored:
        e, temporal, eleg = s["e"], s["temporal"], s["eleg"]
        affinity = s["affinity"]
        if affinity < floor:
            continue
        src, local = _split_native(e["native_id"])
        url = (e.get("metadata") or {}).get("url")
        shared_themes = company_themes & set(e.get("setores") or [])
        tipo = domain_paths.classify_tipo(e)
        matches.append(OpportunityMatch(
            kind=e["kind"], source=src or e.get("source", ""), edital_id=local,
            entity_id=e["native_id"], name=e.get("name") or "",
            description=(e.get("description") or "")[:240],
            score=affinity, affinity=affinity, technical_score=s["best"],
            setores=list(e.get("setores") or []), matched_excerpts=s["excerpts"],
            status=_status_from_temporal(temporal),
            prazo=_prazo_display(temporal), valor=_valor_display(e),
            url=url,
            elegibilidade=eleg,
            **temporal.public_payload(),
            tipo=tipo,
            caminho=domain_paths.build_path(
                e, profile=prof, eleg=eleg, url=url,
                shared_themes=shared_themes, excerpts=s["excerpts"],
            ),
            explicacao=domain_paths.build_explanation(
                tipo, e=e, eleg=eleg, profile=prof,
                has_project=has_project, shared_themes=shared_themes,
            ),
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


def find_ict_partners(profile, *, limit: int = 4) -> list[dict]:
    """Parceiros de P&D (ICTs/laboratórios) que compartilham temas com o perfil.

    ICTs são capacidades/parceiros (spec product-pathways-domain-matching.md),
    NÃO editais: não entram no ranking de afinidade e nunca viram
    "oportunidade". Exigem projeto definido (competência/equipamento só fazem
    sentido com um escopo); sem projeto → []. Best-effort: erro de DB → [].
    Cada item segue o contrato `caminho` com tipo=ict (status "possibilidade").
    """
    from radar.core.kg import entity_catalog

    if not domain_paths.has_project(profile):
        return []
    themes = infer_company_setores(profile)
    if not themes:
        return []
    found: dict[str, dict] = {}
    try:
        for tema in sorted(themes):
            for item in entity_catalog.list_entity_catalog("ict", tema=tema, limit=50):
                eid = item["id"]
                if eid in found:
                    continue
                url = item.get("url") or ""
                e = {
                    "kind": "ict",
                    "native_id": eid,
                    "name": item.get("name") or "",
                    "description": item.get("description") or "",
                    "setores": item.get("themes") or [],
                    "metadata": {"url": url},
                    "capacidades": item.get("capacidades"),
                    "requisitos_texto": [],
                }
                shared = set(item.get("themes") or []) & themes
                found[eid] = {
                    **item,
                    "kind": "ict",
                    "caminho": domain_paths.build_path(
                        e, profile=profile, eleg=None, url=url,
                        shared_themes=shared, excerpts=[],
                    ),
                    "explicacao": domain_paths.build_explanation(
                        domain_paths.PATH_TIPO_ICT, e=e, eleg=None,
                        profile=profile, has_project=True, shared_themes=shared,
                    ),
                }
                if len(found) >= limit:
                    return list(found.values())
    except Exception as err:  # noqa: BLE001 — parceiros são best-effort
        logger.warning("match_v3: ICT partners indisponíveis (%s)", err)
    return list(found.values())


def _rerank_enabled() -> bool:
    return os.getenv("MATCH_RERANK_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _stage3_rerank(prof, matches: list[OpportunityMatch]) -> list[OpportunityMatch]:
    """Reordena SÓ o top-K via radar.core.reranker (RERANK_BACKEND). Falha/indisponível
    → mantém a ordem geométrica (contrato do rerank_scores)."""
    from radar.core.reranker import rerank_scores
    from radar.core.services.eligibility import _profile_get

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
# Trilha investidor — DESATIVADA (spec product-scope-catalog-deactivation.md)
# ===========================================================================

def find_matching_investors(
    profile,
    *,
    workspace_id: str | None = None,
    db=None,
    top_k: int = 5,
    min_score: float | None = None,
    use_hyde: bool = True,
    prepared_company=None,
) -> list[InvestorMatch]:
    """Trilha investidor desativada: investidores privados estão fora do escopo
    ativo. Mantida apenas para compatibilidade de assinatura (routers/eval);
    sempre devolve lista vazia — nenhum fundo é recomendado."""
    logger.debug("match_v3: trilha investidor desativada (find_matching_investors) — retornando []")
    return []


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
