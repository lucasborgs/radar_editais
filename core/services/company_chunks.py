"""Lado EMPRESA do match v3 — `company_chunks` (spec v3-unified §5.4, Fase 2).

Materializa os chunks da empresa por workspace na tabela `company_chunks`
(migration 036, RLS "own" por workspace_id):

  origin='profile'      texto do perfil (CompanyProfile.to_context), chunkado
                        pelo MESMO `_pack_chunks` do gold (simetria de regime
                        com o lado edital — embed cru, sem contexto)
  origin='library_doc'  content_items não-arquivados do workspace (doc_id =
                        id do item)
  origin='hyde'         COLD START (workspace sem documentos): pseudo-doc HyDE
                        gerado do perfil (core/retrieval/hyde.py), regenerado
                        quando o perfil muda

Refresh é ON-DEMAND no match (decisão da Fase 2 — o mais simples que funciona):
`ensure_company_chunks` compara o conjunto desejado de (origin, doc_id, text)
com o que está na tabela e SÓ re-embeda quando divergiu (perfil/library mudou).
Nenhuma task de background; o custo no caminho quente é 1 SELECT de textos.

Escrita/leitura via psycopg (DATABASE_URL) — supabase-py corrompe colunas
`vector` (mesma razão do `_insert_chunks_psycopg` de core/tasks.py). A fronteira
de tenant nas escritas service-side é o próprio workspace_id (postura das tasks);
a RLS da tabela continua sendo a defesa real contra leitura cross-tenant via
PostgREST (leak-test: tests/test_company_chunks_rls.py).

O caminho ANÔNIMO (explore público, eval) não toca a tabela: `ephemeral_company_
chunks` produz os mesmos textos em memória (cache por hash do perfil).
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

import numpy as np

from core.kg.gold import _pack_chunks

logger = logging.getLogger(__name__)

# Perfil "ralo" (cold start): abaixo disso o texto do perfil não sustenta o
# match e o HyDE complementa. Heurística barata — o gate da 1.5 validou o HyDE
# como fallback, não como substituto do perfil rico.
_COLD_START_CHARS = 280


def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL não configurada — o match v3 precisa de acesso direto ao Postgres."
        )
    return dsn


def _vec(v) -> str:
    return "[" + ",".join(f"{float(x):.7f}" for x in v) + "]"


def parse_vec(raw) -> np.ndarray:
    """psycopg devolve `vector` como string crua ("[a,b,c]") sem adapter."""
    if isinstance(raw, str):
        return np.array(raw.strip("[]").split(","), dtype=np.float32)
    return np.asarray(raw, dtype=np.float32)


# ---------------------------------------------------------------------------
# Textos desejados (puro, testável sem DB/LLM)
# ---------------------------------------------------------------------------

def profile_chunk_texts(profile) -> list[str]:
    """Chunks do texto do perfil — mesmo empacotamento do lado edital
    (`_pack_chunks` do gold). Aceita CompanyProfile ou dict."""
    from domain.user_profile import CompanyProfile

    if isinstance(profile, dict):
        allowed = set(CompanyProfile.__dataclass_fields__.keys())
        profile = CompanyProfile(**{k: v for k, v in profile.items() if k in allowed})
    text = profile.to_context()
    if not text.strip() or text.strip() == "Perfil da empresa nao preenchido.":
        return []
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    blocks = [{"section_path": [], "kind": "paragraph", "text": p} for p in paras]
    return [c["text"] for c in _pack_chunks(blocks) if c["text"].strip()]


def _doc_chunk_texts(content: str) -> list[str]:
    paras = [p.strip() for p in (content or "").split("\n") if p.strip()]
    blocks = [{"section_path": [], "kind": "paragraph", "text": p} for p in paras]
    return [c["text"] for c in _pack_chunks(blocks) if c["text"].strip()]


def _hyde_query(profile) -> str:
    """Semente do pseudo-doc HyDE: o resumo mais denso disponível no perfil."""
    from core.services.eligibility import _profile_get
    for f in ("one_liner", "solution_summary", "descricao_atividades", "nome"):
        v = _profile_get(profile, f)
        if v and str(v).strip():
            return str(v).strip()
    return ""


@dataclass
class DesiredChunk:
    origin: str            # profile | library_doc | hyde
    doc_id: str | None
    text: str


def desired_chunks(profile, library_items: list[dict]) -> list[DesiredChunk]:
    """Conjunto-alvo DETERMINÍSTICO de chunks do workspace (perfil + library).
    HyDE fica FORA daqui (não-determinístico) — é tratado à parte no refresh."""
    out: list[DesiredChunk] = []
    out += [DesiredChunk("profile", None, t) for t in profile_chunk_texts(profile)]
    for item in library_items or []:
        for t in _doc_chunk_texts(item.get("content") or ""):
            out.append(DesiredChunk("library_doc", str(item["id"]), t))
    return out


def hyde_wanted(profile, library_items: list[dict]) -> bool:
    """Cold start (§5.4): sem documentos E perfil ralo → HyDE complementa."""
    if library_items:
        return False
    total = sum(len(t) for t in profile_chunk_texts(profile))
    return total < _COLD_START_CHARS and bool(_hyde_query(profile))


# ---------------------------------------------------------------------------
# Refresh on-demand (DB)
# ---------------------------------------------------------------------------

def _load_library_items(db, workspace_id: str) -> list[dict]:
    """content_items não-arquivados. `db` = cliente supabase (RLS do caller ou
    service — a task/rota decide); falha degrada para lista vazia (o perfil
    continua sustentando o match)."""
    if db is None:
        return []
    try:
        rows = (
            db.table("content_items").select("id, content")
            .eq("workspace_id", workspace_id).is_("archived_at", "null")
            .execute().data
        ) or []
        return [r for r in rows if (r.get("content") or "").strip()]
    except Exception as e:  # noqa: BLE001 — library é complemento, não requisito
        logger.warning("company_chunks: falha ao ler content_items (%s)", e)
        return []


def _insert_chunks(cur, workspace_id: str, chunks: list[DesiredChunk], embs) -> None:
    for c, e in zip(chunks, embs, strict=True):
        cur.execute(
            "insert into public.company_chunks "
            "(workspace_id, origin, doc_id, text, embedding) "
            "values (%s, %s, %s, %s, %s::vector)",
            (workspace_id, c.origin, c.doc_id, c.text, _vec(e)),
        )


def ensure_company_chunks(workspace_id: str, profile, *, db=None, conn=None) -> int:
    """Garante que `company_chunks` reflete o perfil+library ATUAIS do workspace.

    Diff barato por (origin, doc_id, text) sobre os chunks DETERMINÍSTICOS
    (profile/library): igual → 0 embeddings, 1 SELECT. Divergiu → delete+insert
    do workspace (atômico) com re-embed. HyDE (cold start) é tratado à parte:
    o pseudo-doc é não-determinístico, então ele NUNCA entra no diff — é
    (re)gerado quando os determinísticos mudam ou quando falta, e apagado
    quando deixa de ser cold start. Falha do HyDE degrada para só-perfil sem
    invalidar o refresh (nada de re-embed perpétuo). Retorna nº de chunks."""
    import psycopg

    from core.retrieval.embedder import embed_texts

    items = _load_library_items(db, workspace_id)
    desired = desired_chunks(profile, items)
    want_hyde = hyde_wanted(profile, items)

    own = conn is None
    if own:
        conn = psycopg.connect(get_dsn(), autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select origin, doc_id, text from public.company_chunks "
                "where workspace_id = %s",
                (workspace_id,),
            )
            current = cur.fetchall()
        cur_det = sorted((o, d or "", t) for o, d, t in current if o != "hyde")
        n_hyde = sum(1 for o, _, _ in current if o == "hyde")
        want_det = sorted((c.origin, c.doc_id or "", c.text) for c in desired)

        det_changed = cur_det != want_det
        hyde_stale = (want_hyde and (n_hyde == 0 or det_changed)) or (not want_hyde and n_hyde > 0)
        if not det_changed and not hyde_stale:
            return len(current)

        to_write = list(desired)
        if want_hyde:
            from core.retrieval.hyde import generate_hyde_doc
            doc = generate_hyde_doc(_hyde_query(profile))
            if doc:
                to_write.append(DesiredChunk("hyde", None, doc))

        embs = embed_texts([c.text for c in to_write]) if to_write else []
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("delete from public.company_chunks where workspace_id = %s",
                        (workspace_id,))
            _insert_chunks(cur, workspace_id, to_write, embs)
        logger.info("company_chunks: workspace=%s refresh (%d chunks, hyde=%s)",
                    workspace_id, len(to_write), want_hyde)
        return len(to_write)
    finally:
        if own:
            conn.close()


def load_company_chunks(workspace_id: str, *, conn=None) -> tuple[list[str], np.ndarray]:
    """(texts, embeddings float32 [n,d]) do workspace — o insumo do Stage 2."""
    import psycopg

    own = conn is None
    if own:
        conn = psycopg.connect(get_dsn(), autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select text, embedding from public.company_chunks "
                "where workspace_id = %s order by origin, doc_id, id",
                (workspace_id,),
            )
            rows = cur.fetchall()
    finally:
        if own:
            conn.close()
    texts = [t for t, _ in rows]
    embs = (np.stack([parse_vec(e) for _, e in rows])
            if rows else np.empty((0, 0), dtype=np.float32))
    return texts, embs


# ---------------------------------------------------------------------------
# Caminho anônimo/efêmero (explore público, eval) — sem tabela
# ---------------------------------------------------------------------------

_EPHEMERAL_CACHE: dict[str, tuple[list[str], np.ndarray]] = {}
_EPHEMERAL_CACHE_MAX = 128


def ephemeral_company_chunks(
    profile, *, use_hyde: bool = True,
) -> tuple[list[str], np.ndarray]:
    """(texts, embeddings) direto do perfil, sem tocar `company_chunks` — o
    caminho do explore anônimo e da eval. Cache in-process por hash dos textos
    (mesma postura do _NODES_CACHE que substitui). `use_hyde=False` torna o
    resultado determinístico (eval)."""
    from core.retrieval.embedder import embed_texts

    texts = profile_chunk_texts(profile)
    if use_hyde and sum(len(t) for t in texts) < _COLD_START_CHARS and _hyde_query(profile):
        from core.retrieval.hyde import generate_hyde_doc
        doc = generate_hyde_doc(_hyde_query(profile))
        if doc:
            texts = texts + [doc]
    if not texts:
        return [], np.empty((0, 0), dtype=np.float32)

    h = hashlib.sha256(("\n".join(texts) + f"|hyde={use_hyde}").encode("utf-8")).hexdigest()
    hit = _EPHEMERAL_CACHE.get(h)
    if hit is not None:
        return hit
    embs = np.asarray(embed_texts(texts), dtype=np.float32)
    if len(_EPHEMERAL_CACHE) >= _EPHEMERAL_CACHE_MAX:
        _EPHEMERAL_CACHE.pop(next(iter(_EPHEMERAL_CACHE)))
    _EPHEMERAL_CACHE[h] = (texts, embs)
    return texts, embs
