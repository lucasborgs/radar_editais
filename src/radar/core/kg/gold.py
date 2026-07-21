"""core/kg/gold.py — ingestor gold v3 (spec docs/specs/v3-unified.md, Fase 1).

Popula as tabelas gold (migration 036) a partir das 4 fontes pré-beta:

  investidores.json  → entities(kind=investidor)          [determinístico, 0 LLM]
  programas.json     → entities(kind=programa) + operado_por + match_chunks
  ict_raw (embrapii) → entities(kind=ict) + credenciada_por(EMBRAPII)
  editais (silver)   → entities(kind=edital) + operado_por + subordinado_a
                       + match_chunks (2 chamadas LLM leves/edital: tagger + constraints)

Caráter: **ingestor com mapeadores determinísticos por fonte + tagger LLM só para
editais.** 3 das 4 fontes já são gold de fábrica. NÃO é extrator — Hyper-Extract
não é tocado; este módulo é ADITIVO (o match v2 segue intocado).

Vocabulários vivem no docs/domain/schema.md (§13), lidos por `radar.core.kg.schema` accessors:
  - setores (taxonomia fechada de 16)   → `normalize_setores`
  - normalização de tags (+ anti_class) → `normalize_tags`
  - seções temáticas/elegibilidade      → `classify_section`
  - constraints §4.4                     → `constraints_producer.produce_from_text`

Idempotente: upsert por (source, native_id); edital cujo `source_hash` não mudou é
pulado; falha em 1 entidade não derruba o batch (transação por entidade);
checkpoint/log a cada 5. LLM via `radar.core.llm.llm_client`; embeddings via
`radar.core.retrieval.embedder` (text-embedding-3-small, 1536d).

match_chunks (Fase 2): o TEXTO armazenado é o silver cru, mas o EMBEDDING é
contextualizado (`radar.core.contextual_retrieval.contextualize_chunks` — o doc de
contexto é o próprio conjunto de blocos temáticos do silver do edital). Decisão
do gate da Fase 1.5: contextual venceu o cru em todas as métricas (MRR
0.505→0.666). Falha por-chunk degrada p/ embed cru (contrato do
contextualize_chunks), nunca derruba o ingest.

Uso (SEMPRE com env local — o .env aponta pro remoto):
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
    OPENAI_API_KEY=... python -m radar.core.kg.gold --limit 5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date
from functools import lru_cache

import psycopg

from radar.core.config import BRONZE_DIR, SILVER_DIR
from radar.core.environment import assert_database_target
from radar.core.kg import schema
from radar.core.kg.canonicalize import anti_class_verdict

logger = logging.getLogger(__name__)

STRUCTURED_DIR = SILVER_DIR / "structured_docs"
EDITAL_SOURCES = ("finep", "fapesp", "fapesc", "web")

# Agência operadora derivada da FONTE do edital (web → desconhecida: sem operado_por).
_SOURCE_AGENCY = {"finep": "FINEP", "fapesp": "FAPESP", "fapesc": "FAPESC"}
# UF da fonte estadual (FINEP/web = nacional/desconhecida → NULL).
_SOURCE_UF = {"fapesp": "SP", "fapesc": "SC"}

# Canonicalização de nomes de agência (operador de programa / fonte de edital).
_AGENCY_CANON = {
    "faps": "FAPs estaduais", "faps estaduais": "FAPs estaduais",
    "finep": "FINEP", "mcti": "MCTI", "sebrae": "Sebrae", "bndes": "BNDES",
    "fapesp": "FAPESP", "fapesc": "FAPESC", "mdic": "MDIC",
    "apexbrasil": "ApexBrasil", "cnpq": "CNPq", "embrapii": "EMBRAPII",
}

# mecanismo (§5.1 CHECK): só estes 5 entram na coluna; `tipo`/`formato` fora do
# enum ficam em metadata (o CHECK proíbe o resto — resolução do conflito §6).
_MECANISMO_ENUM = {"subvencao", "bolsa", "parceria_pd", "premio", "equity"}
_FORMATO_MAP = {
    "edital-periodico": "edital_periodico", "edital_periodico": "edital_periodico",
    "fluxo-continuo": "fluxo_continuo", "fluxo_continuo": "fluxo_continuo",
    "credenciamento": "credenciamento",
}

_CHUNK_CHARS = 1200   # empacotamento de blocos temáticos em match_chunks
_CHUNK_CAP = 60       # teto de chunks por edital
_DESC_CHARS = 2200    # teto da description (embed da entidade)
_TAGGER_CHARS = 9000  # teto do input do tagger LLM


# ===========================================================================
# Utilitários determinísticos (testáveis sem LLM/DB)
# ===========================================================================

def _deburr(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _singularize(tok: str) -> str:
    """Plural pt simples: remove `-s` final (len>4) pulando irregulares comuns
    (-ais/-eis/-ois/-uis/-ns, cujo singular não é só tirar o s)."""
    if len(tok) > 4 and tok.endswith("s") and not tok.endswith(("ais", "eis", "ois", "uis", "ns")):
        return tok[:-1]
    return tok


def normalize_tag(raw: str) -> str | None:
    """Um passe determinístico numa tag: lowercase, trim, sinônimo, singular
    simples (só palavra única). None se vazia/curta demais. NÃO aplica o filtro
    anti-classe (isso é `normalize_tags`)."""
    cfg = schema.tag_normalization()
    syn = {k.lower(): v for k, v in (cfg.get("synonyms") or {}).items()}
    min_len = int(cfg.get("min_len", 2))
    t = _ws(str(raw)).lower() if cfg.get("lowercase", True) else _ws(str(raw))
    if not t or len(t) < min_len:
        return None
    if t in syn:
        return syn[t]
    if cfg.get("singularize", True) and " " not in t:
        t2 = _singularize(t)
        return syn.get(t2, t2)
    return t


def normalize_tags(raw: list[str]) -> list[str]:
    """Passe determinístico + filtro `anti_class_verdict` (genérico/legal/métrica),
    dedup preservando ordem. Aplicado a TODA tag de TODA fonte (§4.3)."""
    out: list[str] = []
    seen: set[str] = set()
    for r in raw or []:
        t = normalize_tag(r)
        if not t or t in seen:
            continue
        if anti_class_verdict(t) is not None:  # None = manter; dict = descartar
            continue
        seen.add(t)
        out.append(t)
    return out


def normalize_setores(raw: list[str]) -> list[str]:
    """Mapeia strings cruas (setores curados, tese_themes, saída do tagger) para a
    taxonomia fechada de 16 (§4.3): 1-3 itens, fallback Multissetorial. Validação
    em aplicação (o CHECK SQL de array_length passa por NULL)."""
    tax = schema.setores_taxonomia()
    labels = list(tax.get("labels") or [])
    fallback = tax.get("fallback", "Multissetorial")
    maxn = int(tax.get("max_por_entidade", 3))
    label_by_deburr = {_deburr(lbl).lower(): lbl for lbl in labels}
    alias = {_deburr(k).lower(): v for k, v in (tax.get("alias_map") or {}).items()}
    theme = {k.lower(): v for k, v in (tax.get("tese_theme_map") or {}).items()}

    out: list[str] = []
    for r in raw or []:
        s = _ws(str(r))
        if not s:
            continue
        mapped = theme.get(s.lower()) or alias.get(_deburr(s).lower()) or label_by_deburr.get(_deburr(s).lower())
        if not mapped:
            continue
        for m in (mapped if isinstance(mapped, list) else [mapped]):
            if m in labels and m not in out:
                out.append(m)
    if not out:
        out = [fallback]
    return out[:maxn]


@lru_cache(maxsize=1)
def _section_rules() -> tuple[frozenset, tuple, tuple]:
    ms = schema.match_sections()
    return (
        frozenset(ms.get("drop_kinds") or []),
        tuple(ms.get("eligibility_patterns") or []),
        tuple(ms.get("boilerplate_patterns") or []),
    )


def classify_section(section_path: list[str] | None, kind: str | None) -> str:
    """Classifica um bloco silver em 'thematic' | 'eligibility' | 'boilerplate'
    (§5.3). Precedência: drop_kinds → eligibility → boilerplate → thematic.
    Os padrões são testados por SEGMENTO do section_path (deburrado/lowercase)."""
    drop_kinds, elig, boil = _section_rules()
    if kind in drop_kinds:
        return "boilerplate"
    segs = [_deburr(s).lower().strip() for s in (section_path or []) if s]
    for seg in segs:
        if any(re.search(p, seg) for p in elig):
            return "eligibility"
    for seg in segs:
        if any(re.search(p, seg) for p in boil):
            return "boilerplate"
    return "thematic"


def _canon_agency(name: str) -> str:
    n = _ws(name)
    return _AGENCY_CANON.get(_deburr(n).lower(), n)


def _split_operador(operador: str | None) -> list[str]:
    """"MCTI / FINEP / FAPs estaduais" → ['MCTI','FINEP','FAPs estaduais']."""
    if not operador:
        return []
    parts = re.split(r"[/;]|(?:\s+e\s+)", operador)
    return [_canon_agency(p) for p in parts if _ws(p)]


def _map_mecanismo(tipo: str | None) -> str | None:
    """`tipo` curado → mecanismo (só se estiver no enum §5.1; senão NULL — o raw
    fica em metadata.tipo)."""
    if not tipo:
        return None
    t = _deburr(tipo).lower().strip()
    return t if t in _MECANISMO_ENUM else None


def _map_formato(formato: str | None) -> str | None:
    if not formato:
        return None
    return _FORMATO_MAP.get(_deburr(formato).lower().strip(), _deburr(formato).lower().replace("-", "_"))


def _infer_mecanismo_from_text(text: str) -> str | None:
    """Inferência determinística e conservadora do mecanismo de edital a partir do
    texto (só quando inequívoco). NULL na dúvida (não inventa)."""
    t = _deburr(text or "").lower()
    if "subvencao economica" in t or "subvencao" in t or "nao reembolsavel" in t or "nao-reembolsavel" in t:
        return "subvencao"
    if "bolsa" in t and "subvencao" not in t:
        return "bolsa"
    if "equity" in t or "participacao acionaria" in t:
        return "equity"
    return None


_UF_RE = re.compile(r",\s*([A-Z]{2})\s*$")
_UF_SET = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)


def _uf_from_address(address: str | None) -> str | None:
    """UF a partir do fim do endereço EMBRAPII ('... , São Paulo, SP' → 'SP')."""
    if not address:
        return None
    m = _UF_RE.search(address.strip())
    if m and m.group(1) in _UF_SET:
        return m.group(1)
    # fallback: última sigla de 2 letras que seja UF válida
    for tok in reversed(re.findall(r"\b([A-Z]{2})\b", address)):
        if tok in _UF_SET:
            return tok
    return None


def _normalize_status(raw: str | None, deadline: date | None) -> str | None:
    """status cru da fonte → {aberta, encerrada} (editais). Regra WIKI §5.9-status:
    deadline futura → aberta; raw ENCERRADA → encerrada; senão heurística leve."""
    r = (raw or "").strip().upper()
    if deadline is not None:
        return "aberta" if deadline >= date.today() else "encerrada"
    if r in ("ABERTA", "ABERTO"):
        return "aberta"
    if r in ("ENCERRADA", "ENCERRADO", "FECHADA", "FECHADO"):
        return "encerrada"
    return None


def _pack_chunks(blocks: list[dict]) -> list[dict]:
    """Empacota blocos temáticos em match_chunks de ~_CHUNK_CHARS, preservando o
    section_path/kind do primeiro bloco do chunk. Teto _CHUNK_CAP."""
    chunks: list[dict] = []
    buf: list[str] = []
    head: dict | None = None
    size = 0

    def _flush():
        nonlocal buf, head, size
        if buf and head is not None:
            chunks.append({
                "section_path": head.get("section_path") or [],
                "kind": head.get("kind"),
                "text": "\n".join(buf).strip(),
            })
        buf, head, size = [], None, 0

    for b in blocks:
        txt = _ws(b.get("text") or "")
        if not txt:
            continue
        if head is None:
            head = b
        if size and size + len(txt) > _CHUNK_CHARS:
            _flush()
            head = b
        buf.append(txt)
        size += len(txt) + 1
        if len(chunks) >= _CHUNK_CAP:
            break
    _flush()
    return chunks[:_CHUNK_CAP]


def _embed_match_chunks(chunks: list[dict]) -> list[list[float]]:
    """Embeddings dos match_chunks com enriquecimento contextual (Fase 2).

    O texto ARMAZENADO segue cru (coluna `text`); só o vetor leva o contexto —
    mesma convenção de `edital_chunks`. O doc de contexto é a concatenação dos
    próprios chunks (o recorte temático do silver), exatamente a célula
    `contextual` que venceu o gate da Fase 1.5."""
    from radar.core.contextual_retrieval import contextualize_chunks
    from radar.core.retrieval.embedder import embed_texts

    if not chunks:
        return []
    return embed_texts(contextualize_chunks(chunks))


# ===========================================================================
# Leitura de silver / bronze
# ===========================================================================

def _read_silver_blocks(source: str, stem: str) -> list[dict]:
    path = STRUCTURED_DIR / source / f"{stem}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _read_silver_hash(source: str, stem: str) -> str | None:
    path = STRUCTURED_DIR / source / f"{stem}.meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("source_hash")
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=8)
def _load_bronze(source: str) -> list[dict]:
    """Bronze cru da fonte (snapshot mais recente; web é aditivo por url_hash)."""
    bdir = BRONZE_DIR / f"{source}_raw"
    if not bdir.exists():
        return []
    files = sorted(bdir.glob("*.json"))
    if not files:
        return []
    if source == "web":
        by_hash: dict[str, dict] = {}
        for f in files:
            try:
                for it in json.loads(f.read_text(encoding="utf-8")):
                    if it.get("url_hash"):
                        by_hash[it["url_hash"]] = it
            except Exception:  # noqa: BLE001
                continue
        return list(by_hash.values())
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


_FAPESP_ID_RE = re.compile(r"/(\d+)(?:/|$)")


def _native_of(source: str, rec: dict) -> str | None:
    """Chave nativa (stem do silver) de um registro bronze — inverso de
    `_bronze_record`. None quando o registro não tem a chave da fonte."""
    if source == "finep":
        cid = rec.get("chamada_id")
        return str(cid) if cid is not None else None
    if source == "fapesc":
        return rec.get("native_id") or None
    if source == "web":
        return rec.get("url_hash") or None
    if source == "fapesp":
        m = _FAPESP_ID_RE.search((rec.get("url") or "").rstrip("/"))
        return m.group(1) if m else None
    return None


def iter_bronze_editais():
    """Itera `(source, native)` sobre todo edital presente no bronze scraped.

    Enumeração AUTORITATIVA de "quais editais existem agora" — o bronze é o que os
    scrapers acabaram de materializar, sem depender de artefato derivado (silver,
    gold ou hipergrado). Usada pelo ETL diário para construir o silver antes do
    `ingest_all`. Dedup por `(source, native)`."""
    for source in EDITAL_SOURCES:
        seen: set[str] = set()
        for rec in _load_bronze(source):
            native = _native_of(source, rec)
            if native and native not in seen:
                seen.add(native)
                yield source, native


def _bronze_record(source: str, stem: str) -> dict | None:
    """Registro bronze do edital, casado pela chave nativa da fonte."""
    recs = _load_bronze(source)
    for r in recs:
        if _native_of(source, r) == stem:
            return r
    return None


def _edital_metadata(source: str, stem: str, blocks: list[dict]) -> dict:
    """Metadados DETERMINÍSTICOS do edital a partir do bronze da fonte (§6 passo 1).
    Campo ausente → NULL (não inventa com LLM)."""
    rec = _bronze_record(source, stem) or {}
    title = _ws(rec.get("titulo") or rec.get("title") or "")
    if not title:  # fallback: primeiro heading do silver
        title = next((_ws(b["text"]) for b in blocks if b.get("kind") == "heading" and b.get("text")), stem)

    # deadline: FINEP dd/mm/yyyy; FAPESP/FAPESC ISO.
    deadline: date | None = None
    if source == "finep":
        deadline = schema.parse_deadline(rec.get("prazo_envio"))
    else:
        dl = rec.get("data_limite")
        if dl:
            try:
                deadline = date.fromisoformat(str(dl)[:10])
            except ValueError:
                deadline = None

    url = _ws(rec.get("link") or rec.get("url") or "")
    meta = {
        "url": url or None,
        "tema": rec.get("tema") or None,
        "publico_alvo": rec.get("publico_alvo") or None,
        "modalidades": rec.get("modalidades") or None,
    }
    return {
        "name": title,
        "status": _normalize_status(rec.get("status"), deadline),
        "deadline": deadline,
        "uf": _SOURCE_UF.get(source),
        "descricao_bronze": _ws(rec.get("descricao") or ""),
        "metadata": {k: v for k, v in meta.items() if v},
    }


# ===========================================================================
# Tagger LLM (call A) — editais
# ===========================================================================

_TAGGER_SYSTEM = """Você classifica um edital brasileiro de fomento à inovação em \
SETORES (taxonomia fechada) e TECNOLOGIAS (folksonomia curta), a partir do texto das \
seções temáticas.

setores: escolha de 1 a 3 EXATAMENTE desta lista (nada fora): {labels}. Use \
"Multissetorial" quando o edital for transversal/genérico.
tecnologias_tags: até 8 tags curtas de tecnologia/tema específico (minúsculas, \
singular, sem repetir setor). Só o que o texto sustenta; [] se nada específico.

Responda JSON: {{"setores": ["..."], "tecnologias_tags": ["..."]}}."""


def _tag_edital(thematic_text: str, *, client, model: str) -> tuple[list[str], list[str]]:
    """Call A: texto temático → (setores_raw, tags_raw). Fail-open → ([], [])."""
    if not thematic_text.strip():
        return [], []
    labels = ", ".join(schema.setores_taxonomia().get("labels") or [])
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _TAGGER_SYSTEM.format(labels=labels)},
                {"role": "user", "content": thematic_text[:_TAGGER_CHARS]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:  # noqa: BLE001 — tagger nunca derruba o ingest
        logger.warning("_tag_edital: falha (%s) — sem tags", e)
        return [], []
    setores = [str(s) for s in (data.get("setores") or []) if s]
    tags = [str(t) for t in (data.get("tecnologias_tags") or []) if t]
    return setores, tags


# ===========================================================================
# DB — conexão + upserts
# ===========================================================================

def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "gold.ingest_all: DATABASE_URL não definida. Rode com o Postgres LOCAL "
            "(postgresql://postgres:postgres@127.0.0.1:54322/postgres) — o .env aponta pro remoto."
        )
    return dsn


def _vec(v: list[float] | None) -> str | None:
    return None if v is None else "[" + ",".join(repr(float(x)) for x in v) + "]"


_ENTITY_UPSERT = """
insert into public.entities
  (kind, source, native_id, name, description, mecanismo, formato, setores, tecnologias_tags,
   status, deadline, uf, ticket_min, ticket_max, constraints, requisitos_texto, curated,
   verificado_em, metadata, embedding, updated_at)
values
  (%(kind)s, %(source)s, %(native_id)s, %(name)s, %(description)s, %(mecanismo)s, %(formato)s,
   %(setores)s, %(tags)s, %(status)s, %(deadline)s, %(uf)s, %(ticket_min)s, %(ticket_max)s,
   %(constraints)s::jsonb, %(requisitos)s, %(curated)s, %(verificado)s, %(metadata)s::jsonb,
   %(embedding)s::vector, now())
on conflict (source, native_id) do update set
  kind=excluded.kind, name=excluded.name, description=excluded.description,
  mecanismo=excluded.mecanismo, formato=excluded.formato, setores=excluded.setores,
  tecnologias_tags=excluded.tecnologias_tags, status=excluded.status, deadline=excluded.deadline,
  uf=excluded.uf, ticket_min=excluded.ticket_min, ticket_max=excluded.ticket_max,
  constraints=excluded.constraints, requisitos_texto=excluded.requisitos_texto,
  curated=excluded.curated, verificado_em=excluded.verificado_em, metadata=excluded.metadata,
  embedding=excluded.embedding, updated_at=now()
returning id
"""


def _upsert_entity(cur, **f) -> str:
    params = {
        "kind": f["kind"], "source": f["source"], "native_id": f["native_id"],
        "name": f["name"], "description": f.get("description") or "",
        "mecanismo": f.get("mecanismo"), "formato": f.get("formato"),
        "setores": f.get("setores") or [], "tags": f.get("tecnologias_tags") or [],
        "status": f.get("status"), "deadline": f.get("deadline"), "uf": f.get("uf"),
        "ticket_min": f.get("ticket_min"), "ticket_max": f.get("ticket_max"),
        "constraints": json.dumps(f.get("constraints") or []),
        "requisitos": f.get("requisitos_texto") or [],
        "curated": bool(f.get("curated", False)), "verificado": f.get("verificado_em"),
        "metadata": json.dumps(f.get("metadata") or {}, ensure_ascii=False),
        "embedding": _vec(f.get("embedding")),
    }
    cur.execute(_ENTITY_UPSERT, params)
    return cur.fetchone()[0]


def _upsert_rel(cur, source_id: str, target_id: str, rtype: str, properties: dict | None = None) -> None:
    cur.execute(
        "insert into public.entity_relationships (source_id, target_id, type, properties) "
        "values (%s, %s, %s, %s::jsonb) on conflict (source_id, target_id, type) do nothing",
        (source_id, target_id, rtype, json.dumps(properties or {})),
    )


def _replace_match_chunks(cur, entity_id: str, chunks: list[dict], embeddings: list[list[float]]) -> int:
    cur.execute("delete from public.match_chunks where entity_id = %s", (entity_id,))
    n = 0
    for i, (c, emb) in enumerate(zip(chunks, embeddings, strict=True)):
        cur.execute(
            "insert into public.match_chunks (entity_id, idx, section_path, kind, text, embedding) "
            "values (%s, %s, %s, %s, %s, %s::vector)",
            (entity_id, i, c.get("section_path") or [], c.get("kind"), c["text"], _vec(emb)),
        )
        n += 1
    return n


def _get_agency(cur, cache: dict[str, str], name: str) -> str | None:
    """Upsert idempotente de uma agência (kind=agencia). Retorna o id (cacheado
    no run). Nome vazio → None."""
    from radar.core.retrieval.embedder import embed_query

    canon = _canon_agency(name)
    if not canon:
        return None
    if canon in cache:
        return cache[canon]
    nid = f"agencia:{schema.slugify(canon)}"
    eid = _upsert_entity(
        cur, kind="agencia", source="curadoria", native_id=nid, name=canon,
        description=canon, curated=True, embedding=embed_query(canon),
    )
    cache[canon] = eid
    return eid


# ===========================================================================
# Mapeadores por fonte
# ===========================================================================

def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def _ticket_from_range(rng) -> tuple[float | None, float | None]:
    if isinstance(rng, dict):
        return _num(rng.get("min_brl") or rng.get("min")), _num(rng.get("max_brl") or rng.get("max"))
    return None, None


def _parse_date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _ingest_investidores(conn, stats: dict) -> None:
    from radar.core.retrieval.embedder import embed_query

    path = SILVER_DIR / "investidores.json"
    recs = json.loads(path.read_text(encoding="utf-8")).get("investidores", []) if path.exists() else []
    for r in recs:
        try:
            desc = _ws(r.get("tese") or "")
            with conn.transaction(), conn.cursor() as cur:
                _upsert_entity(
                    cur, kind="investidor", source="curadoria", native_id=r["id"],
                    name=r.get("name") or r["id"], description=desc, mecanismo="equity",
                    setores=normalize_setores(list(r.get("setores") or []) + list(r.get("tese_themes") or [])),
                    tecnologias_tags=normalize_tags(r.get("tese_keywords") or []),
                    status="ativa" if r.get("fund_status") == "ativo" else "inativa",
                    ticket_min=_ticket_from_range(r.get("ticket_range"))[0],
                    ticket_max=_ticket_from_range(r.get("ticket_range"))[1],
                    curated=True, verificado_em=_parse_date(r.get("verificado_em")),
                    metadata={
                        "estagio_alvo": r.get("estagio_alvo") or [],
                        # `setores` da coluna é vocabulário normalizado (1–3
                        # itens) para matching. A ficha factual precisa manter
                        # todas as verticais declaradas pelo investidor.
                        "verticais": list(r.get("setores") or []),
                        "lead_follow": r.get("lead_follow"), "generalista": r.get("generalista"),
                        "fund_status": r.get("fund_status"), "site": r.get("site"),
                        # Preservados p/ o card de escrita (pitch p/ fundo) — não
                        # existem em coluna, só o writing os lê (get_investidor).
                        "portfolio": list(r.get("portfolio") or []),
                        "co_investidores": list(r.get("co_investidores") or []),
                        "tese_themes": list(r.get("tese_themes") or []),
                    },
                    embedding=embed_query(desc or r.get("name") or r["id"]),
                )
            stats["investidor"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("investidor %s falhou: %s", r.get("id"), e)
            stats["errors"] += 1
    logger.info("investidores: %d ok", stats["investidor"])


def _ingest_programas(conn, agency_cache: dict, stats: dict) -> None:
    from radar.core.kg.constraints_producer import produce_from_text
    from radar.core.retrieval.embedder import embed_query

    path = SILVER_DIR / "programas.json"
    recs = json.loads(path.read_text(encoding="utf-8")).get("programas", []) if path.exists() else []
    for r in recs:
        try:
            desc = _ws(" ".join(x for x in [r.get("descricao"), r.get("beneficio")] if x))
            tmin, tmax = _ticket_from_range(r.get("ticket_range"))
            constraints, requisitos, exclusoes, publico_alvo = produce_from_text(r.get("elegibilidade") or "")
            chunks = _pack_chunks([{"section_path": [r.get("name") or ""], "kind": "paragraph", "text": desc}])
            chunk_embs = _embed_match_chunks(chunks)
            with conn.transaction(), conn.cursor() as cur:
                eid = _upsert_entity(
                    cur, kind="programa", source="curadoria", native_id=r["id"],
                    name=r.get("name") or r["id"], description=desc,
                    mecanismo=_map_mecanismo(r.get("tipo")), formato=_map_formato(r.get("formato")),
                    setores=normalize_setores(list(r.get("setores") or []) + list(r.get("tese_themes") or [])),
                    tecnologias_tags=normalize_tags(r.get("tese_themes") or []),
                    status="ativa" if r.get("status") == "ativo" else "inativa",
                    ticket_min=tmin, ticket_max=tmax, constraints=constraints,
                    requisitos_texto=requisitos, curated=True,
                    verificado_em=_parse_date(r.get("verificado_em")),
                    metadata={
                        "operador": r.get("operador"), "tipo": r.get("tipo"),
                        "cadencia": r.get("cadencia"), "beneficio": r.get("beneficio"),
                        "elegibilidade": r.get("elegibilidade"), "estagio_alvo": r.get("estagio_alvo") or [],
                        "site": r.get("site"), "faq_url": r.get("faq_url"),
                        "exclusoes": exclusoes, "publico_alvo": publico_alvo,
                    },
                    embedding=embed_query(desc or r.get("name") or r["id"]),
                )
                if chunks:
                    _replace_match_chunks(cur, eid, chunks, chunk_embs)
                    stats["match_chunks"] += len(chunks)
                for ag in _split_operador(r.get("operador")):
                    aid = _get_agency(cur, agency_cache, ag)
                    if aid:
                        _upsert_rel(cur, eid, aid, "operado_por")
                        stats["operado_por"] += 1
            stats["programa"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("programa %s falhou: %s", r.get("id"), e)
            stats["errors"] += 1
    logger.info("programas: %d ok", stats["programa"])


def _ingest_icts(conn, agency_cache: dict, stats: dict) -> None:
    from radar.core.retrieval.embedder import embed_query

    files = sorted((BRONZE_DIR / "ict_raw").glob("embrapii_*.json"))
    recs = json.loads(files[-1].read_text(encoding="utf-8")) if files else []
    for r in recs:
        try:
            slug = r.get("slug") or schema.slugify(r.get("name") or "")
            desc = _ws(r.get("about") or "")
            areas = list(r.get("areas_raw") or [])
            with conn.transaction(), conn.cursor() as cur:
                eid = _upsert_entity(
                    cur, kind="ict", source="embrapii", native_id=f"embrapii:{slug}",
                    name=r.get("name") or slug, description=desc[:_DESC_CHARS],
                    setores=normalize_setores(areas),
                    tecnologias_tags=normalize_tags(areas),
                    uf=_uf_from_address(r.get("address")), curated=True,
                    metadata={
                        "institution_type": r.get("institution_type"), "contact": r.get("contact") or {},
                        "url": r.get("url"), "address": r.get("address"), "embrapii_kind": r.get("kind"),
                    },
                    embedding=embed_query(desc or r.get("name") or slug),
                )
                aid = _get_agency(cur, agency_cache, "EMBRAPII")
                if aid:
                    _upsert_rel(cur, eid, aid, "credenciada_por")
                    stats["credenciada_por"] += 1
            stats["ict"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("ict %s falhou: %s", r.get("slug"), e)
            stats["errors"] += 1
    logger.info("icts: %d ok", stats["ict"])


@lru_cache(maxsize=1)
def _programa_detection() -> dict[str, str]:
    """Token distintivo → programa native_id (para subordinado_a). Data-driven de
    programas.json; conservador (só marca brand inequívoca, len≥6, sem colisão)."""
    path = SILVER_DIR / "programas.json"
    if not path.exists():
        return {}
    stop = {"programa", "brasil", "startup", "startups", "pequenas", "empresas",
            "pesquisa", "inovacao", "sebrae", "finep", "bndes", "fapesp", "apexbrasil", "mdic"}
    tok_to_ids: dict[str, set[str]] = defaultdict(set)
    for p in json.loads(path.read_text(encoding="utf-8")).get("programas", []):
        for tok in re.findall(r"\w+", _deburr(p.get("name") or "").lower()):
            if len(tok) >= 6 and tok not in stop:
                tok_to_ids[tok].add(p["id"])
    return {tok: next(iter(ids)) for tok, ids in tok_to_ids.items() if len(ids) == 1}


def _detect_programa(title: str) -> str | None:
    toks = set(re.findall(r"\w+", _deburr(title or "").lower()))
    det = _programa_detection()
    for tok, pid in det.items():
        if tok in toks:
            return pid
    return None


def _existing_hash(cur, source: str, native_id: str) -> str | None:
    cur.execute(
        "select metadata->>'source_hash' from public.entities where source=%s and native_id=%s",
        (source, native_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _programa_id_map(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("select native_id, id from public.entities where kind='programa'")
        return {nid: eid for nid, eid in cur.fetchall()}


def _ingest_editais(
    conn,
    agency_cache: dict,
    stats: dict,
    *,
    limit: int | None,
    skip_unchanged: bool,
    edital_ids: list[str] | None = None,
) -> None:
    from radar.core.kg.constraints_producer import produce_from_text
    from radar.core.llm.llm_client import make_client
    from radar.core.retrieval.embedder import embed_query

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = make_client(api_key=os.environ["OPENAI_API_KEY"], max_retries=6)
    prog_ids = _programa_id_map(conn)

    # enumera editais (source, stem)
    todo: list[tuple[str, str]] = []
    for src in EDITAL_SOURCES:
        d = STRUCTURED_DIR / src
        if d.exists():
            todo += [(src, f.stem) for f in sorted(d.glob("*.jsonl"))]
    if edital_ids:
        requested = set(edital_ids)
        todo = [(src, stem) for src, stem in todo if f"{src}:{stem}" in requested]
    if limit:
        todo = todo[:limit]

    for n, (src, stem) in enumerate(todo, 1):
        native_id = f"{src}:{stem}"
        try:
            blocks = _read_silver_blocks(src, stem)
            if not blocks:
                logger.info("edital %s sem silver — pulado", native_id)
                stats["skipped"] += 1
                continue
            src_hash = _read_silver_hash(src, stem)
            if skip_unchanged and src_hash:
                with conn.cursor() as cur:
                    if _existing_hash(cur, src, native_id) == src_hash:
                        stats["skipped"] += 1
                        continue

            thematic = [b for b in blocks if classify_section(b.get("section_path"), b.get("kind")) == "thematic"]
            elig_text = "\n".join(
                _ws(b.get("text") or "") for b in blocks
                if classify_section(b.get("section_path"), b.get("kind")) == "eligibility"
            ).strip()
            thematic_text = "\n".join(_ws(b.get("text") or "") for b in thematic).strip()

            md = _edital_metadata(src, stem, blocks)
            description = (md["descricao_bronze"] or thematic_text)[:_DESC_CHARS]

            setores_raw, tags_raw = _tag_edital(thematic_text, client=client, model=model)
            constraints, requisitos, exclusoes, publico_alvo = produce_from_text(elig_text)

            chunks = _pack_chunks(thematic)
            chunk_embs = _embed_match_chunks(chunks)
            emb = embed_query(description or md["name"])

            meta = dict(md["metadata"])
            meta["source_hash"] = src_hash
            # Campos de display do card (call B): exclusoes/publico_alvo vêm do
            # LLM sobre as seções de elegibilidade. `publico_alvo` (lista tipada)
            # substitui o `publico_alvo` cru do bronze (string livre) — o card
            # espera lista; se o LLM não delimitou, cai no bronze como 1 item.
            bronze_pa = md["metadata"].get("publico_alvo")
            meta["exclusoes"] = exclusoes
            meta["publico_alvo"] = publico_alvo or ([str(bronze_pa)] if bronze_pa else [])

            with conn.transaction(), conn.cursor() as cur:
                eid = _upsert_entity(
                    cur, kind="edital", source=src, native_id=native_id, name=md["name"],
                    description=description, mecanismo=_infer_mecanismo_from_text(md["descricao_bronze"] or thematic_text),
                    setores=normalize_setores(setores_raw), tecnologias_tags=normalize_tags(tags_raw),
                    status=md["status"], deadline=md["deadline"], uf=md["uf"],
                    constraints=constraints, requisitos_texto=requisitos, curated=False,
                    metadata=meta, embedding=emb,
                )
                if chunks:
                    _replace_match_chunks(cur, eid, chunks, chunk_embs)
                    stats["match_chunks"] += len(chunks)
                ag = _SOURCE_AGENCY.get(src)
                if ag:
                    aid = _get_agency(cur, agency_cache, ag)
                    if aid:
                        _upsert_rel(cur, eid, aid, "operado_por")
                        stats["operado_por"] += 1
                pid = _detect_programa(md["name"])
                if pid and pid in prog_ids:
                    _upsert_rel(cur, eid, prog_ids[pid], "subordinado_a")
                    stats["subordinado_a"] += 1
            stats["edital"] += 1
        except Exception as e:  # noqa: BLE001 — 1 edital não derruba o batch
            logger.warning("edital %s falhou: %s", native_id, e)
            stats["errors"] += 1
        if n % 5 == 0:
            logger.info("editais: %d/%d processados (ok=%d, skip=%d, err=%d)",
                        n, len(todo), stats["edital"], stats["skipped"], stats["errors"])
    logger.info("editais: %d ok, %d pulados, %d erros", stats["edital"], stats["skipped"], stats["errors"])


# ===========================================================================
# Orquestrador
# ===========================================================================

def ingest_all(
    *,
    sources: list[str] | None = None,
    limit: int | None = None,
    skip_unchanged: bool = True,
    edital_ids: list[str] | None = None,
) -> dict:
    """Ingesta as 4 fontes pré-beta nas tabelas gold. `sources` filtra
    ('investidor','programa','ict','edital'); `limit` corta os editais (debug).
    Retorna contadores por kind/aresta/match_chunks/erros."""
    want = set(sources or ["investidor", "programa", "ict", "edital"])
    stats: dict = defaultdict(int)
    agency_cache: dict[str, str] = {}
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        if "investidor" in want:
            _ingest_investidores(conn, stats)
        if "programa" in want:
            _ingest_programas(conn, agency_cache, stats)
        if "ict" in want:
            _ingest_icts(conn, agency_cache, stats)
        if "edital" in want:
            _ingest_editais(
                conn,
                agency_cache,
                stats,
                limit=limit,
                skip_unchanged=skip_unchanged,
                edital_ids=edital_ids,
            )
    stats["agencia"] = len(agency_cache)
    logger.info("ingest_all concluído: %s", dict(stats))
    return dict(stats)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Ingest gold v3 (spec v3-unified §6)")
    ap.add_argument("--source", action="append", dest="sources",
                    choices=["investidor", "programa", "ict", "edital"],
                    help="restringe a fonte (repetível); default = todas")
    ap.add_argument("--limit", type=int, default=None, help="corta os editais (debug)")
    ap.add_argument(
        "--edital-id",
        action="append",
        dest="edital_ids",
        help="restringe a ingestão de editais ao ID canônico (repetível)",
    )
    ap.add_argument("--no-skip", action="store_true", help="reprocessa editais mesmo com source_hash igual")
    args = ap.parse_args()
    assert_database_target("gold ingest")
    out = ingest_all(
        sources=args.sources,
        limit=args.limit,
        skip_unchanged=not args.no_skip,
        edital_ids=args.edital_ids,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
