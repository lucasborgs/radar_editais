"""core/kg/spike/extractor.py — Fase 2: extração LLM de relações semânticas.

SPEC §9. Extrai relações NÃO deriváveis das colunas do gold (ex.:
`potencial_parceria` edital↔ICT), guiada pelas Categorias de Aristóteles.

Contrato:
  - Entrada: texto já extraído (silver `structured_docs/*.jsonl` +
    `description`/`requisitos_texto` do gold) — NUNCA re-lê a fonte crua.
  - Prompt organizado pelas Categorias (Relação/Qualidade/Quantidade/Posição/
    Lugar/Tempo/Estado/Ação/Paixão) — não um CHECK, um guia.
  - Saída: triplas `{subject_ref, predicate, object_ref|object_literal}`.
    `object_ref` resolvido contra `kg_spike.nodes`/`quality_nodes` vira aresta
    em `kg_spike.edges` (source='fase2_llm'); o que não resolve (literal ou
    entidade ausente) vai para a CAUDA ABERTA (`extraction_candidates`,
    `core=false`) até o gate de evidência decidir.
  - Gate de evidência: predicado novo só promove a `core=true` com **≥3
    evidências independentes** (≥3 subjects distintos — regra schema.md §5.9).
    Aplica-se via `promote()`; até lá o predicado fica `core=false`.
  - Idempotência: hash por entidade (texto + modelo) — só reprocessa o que
    mudou; falha por-entidade não derruba o batch.
  - Modelo: tier barato (`OPENAI_MODEL`/`LLM_BACKEND`), mesmo padrão do tagger.

Uso:
    DATABASE_URL=... OPENAI_API_KEY=... python -m radar.core.kg.spike.extractor
    python -m radar.core.kg.spike.extractor --promote-only   # só o gate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import unicodedata
from typing import Any

from radar.core.config import SILVER_DIR
from radar.core.kg.spike import graph_store
from radar.core.kg.spike.graph_store import SCHEMA, connect

logger = logging.getLogger(__name__)

STRUCTURED_DIR = SILVER_DIR / "structured_docs"
EDITAL_SOURCES = ("finep", "fapesp", "fapesc", "web")

# Categorias que guiam o prompt do extrator (SPEC §9) — guia, não constraint.
_CATEGORIES_GUIDE = (
    "Relação (conexão com outro agente/entidade: opera, financia, credencia, exige parceria)\n"
    "Qualidade (características restritivas: exige setor X, requer tecnologia Y)\n"
    "Quantidade (valores: teto de faturamento, porte, faixa de investimento)\n"
    "Posição (estágio/disposição: estágio de maturidade exigido, TRL)\n"
    "Lugar (localização: UF, abrangência)\n"
    "Tempo (prazo, vigência)\n"
    "Estado (condição/hábito da entidade)\n"
    "Ação (o que a entidade faz / habilita o proponente a fazer)\n"
    "Paixão (o que a entidade recebe/sofre: recebe submissões, é financiado)"
)

_SYSTEM_PROMPT = f"""Você extrai relações SEMÂNTICAS de textos de oportunidades de fomento à inovação
(editais, chamadas, páginas de programas e ICTs). Você NÃO reestrutura o texto: apenas
emite triplas que conectam a entidade a outras entidades/valores.

Guia de categorias (Aristóteles — organize suas triplas por estas dimensões):
{_CATEGORIES_GUIDE}

REGRAS:
- subject_ref SEMPRE é a entidade em análise (id do nó fornecido).
- predicate: verbo/nome da RELAÇÃO CONCRETA, kebab-case (ex.: potencial_parceria,
  exige_parceria_com, financia, opera, credencia). NUNCA use nomes de categoria
  ("qualidade", "quantidade", "tempo", "posição") como predicate, e nunca use
  placeholders com "_x" (ex.: exige_setor_X) — se o objeto for um setor genérico,
  use exige_setor com o valor no object_literal.
- object_ref quando a outra ponta for uma ENTIDADE nomeada (ICT, agência, programa, investidor);
  object_literal quando for valor (UF, estágio, valor em R$, número de TRL).
- Só emita relações com EVIDÊNCIA explícita no texto. Não invente.
- Emita no máximo 10 triplas. Priorize relações entre agentes (potencial_parceria, opera,
  credencia, financia) e qualidades restritivas explícitas.
- Responda APENAS JSON: {{"triples": [{{"predicate", "subject_ref", "object_ref"|"object_literal", "evidence"}}]}}
"""

_EXTRACT_CHARS = 9000  # teto do input (mesmo do tagger do gold)

EVIDENCE_GATE = 3  # evidências independentes para promover predicado a core=true

# Predicados que o LLM NUNCA deve emitir como relação — nomes de categoria
# aristotélica vazando para verbo (ex.: "quantidade", "tempo") e placeholders
# `_X` (ex.: exige_setor_X). Guard de qualidade: filtrados antes do grafo.
_GUARDED_PREDICATES = frozenset({
    "relacao", "qualidade", "quantidade", "posicao", "posição",
    "lugar", "tempo", "estado", "acao", "ação", "paixao", "paixão",
    "categoria", "quantidade_maxima", "localizacao", "exige_setor_x",
})


def _is_guarded_predicate(predicate: str) -> bool:
    p = (predicate or "").strip().lower()
    if p in _GUARDED_PREDICATES:
        return True
    return p.endswith("_x")


def _deburr(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).strip().lower()


def _read_silver_text(source: str, stem: str) -> tuple[str, str | None]:
    """(texto temático do silver, source_hash). Reusa o artifact silver — sem
    re-extração da fonte crua."""
    path = STRUCTURED_DIR / source / f"{stem}.jsonl"
    if not path.exists():
        return "", None
    blocks: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            blk = json.loads(line)
        except json.JSONDecodeError:
            continue
        if blk.get("kind") in ("boilerplate", "signature"):
            continue
        blocks.append((blk.get("section_path") or ["-"]).__str__() + " " + (blk.get("text") or ""))
    meta_path = path.with_suffix(".meta.json")
    src_hash = None
    if meta_path.exists():
        try:
            src_hash = json.loads(meta_path.read_text(encoding="utf-8")).get("source_hash")
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(blocks), src_hash


def _load_gold_text(cur, native_id: str) -> str:
    """`description` + `requisitos_texto` do gold para o subject (SPEC §9).

    Complementa o silver — texto curado/informacional que o chunk estrutural
    não carrega. Fail-open → "" (só silver)."""
    try:
        cur.execute(
            "select description, requisitos_texto from public.entities where native_id=%s",
            (native_id,),
        )
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return ""
    if not row:
        return ""
    desc, reqs = row
    parts = [p for p in [desc, *(reqs or [])] if p]
    return "\n".join(parts)


def _extract_llm(client, model: str, subject_id: str, text: str) -> list[dict[str, Any]]:
    """Chamada LLM → triplas. Fail-open → [] (nunca derruba o batch)."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"subject_ref: {subject_id}\n\n{text[:_EXTRACT_CHARS]}"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("kg_spike extractor: falha LLM para %s (%s) — sem triplas", subject_id, e)
        return []
    triples = data.get("triples") or []
    return [
        t for t in triples
        if isinstance(t, dict) and t.get("predicate") and not _is_guarded_predicate(t["predicate"])
    ]


def _hash_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{text}|{model}".encode()).hexdigest()


def _existing_hash(subject_id: str) -> str | None:
    """Último source_hash processado para o subject (idempotência)."""
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"select source_hash from {SCHEMA}.extraction_candidates "
                    "where subject_id=%s order by created_at desc limit 1",
                    (subject_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Resolução object_ref → node_id (SPEC §9)
# ─────────────────────────────────────────────────────────────────────────────

def _load_resolvers(cur) -> tuple[dict[str, str], dict[str, str]]:
    """(by_native, by_name) para nodes + quality_nodes.

    Resolve `object_ref` das triplas contra o grafo já materializado (Fase 1).
    `by_native` casa `native_id` exato (ex.: `finep:589`); `by_name` casa nome
    deburred/case-insensitive (ex.: "ICT CEIA-UFG" → `ict:embrapii:ceia-ufg`).
    """
    by_native: dict[str, str] = {}
    by_name: dict[str, str] = {}
    cur.execute(f"select id, kind, native_id, name from {SCHEMA}.nodes")
    for nid, _kind, native, name in cur.fetchall():
        if native:
            by_native[native] = nid
        by_name[nid] = nid
        by_name[_deburr(name)] = nid
    cur.execute(f"select id, family, value from {SCHEMA}.quality_nodes")
    for qid, _family, value in cur.fetchall():
        by_name[_deburr(value)] = qid
        by_name[f"{qid}"] = qid
    return by_native, by_name


def resolve_object_ref(ref: str | None, by_native: dict[str, str], by_name: dict[str, str]) -> str | None:
    """Resolve `object_ref` de uma tripla para um node_id do spike, ou None.

    Ordem de tentativa: id exato do nó (ex.: `edital:finep:589`) → `native_id`
    (ex.: `finep:589`) → nome deburred (ex.: "ICT CEIA-UFG"). None = vai para a
    cauda aberta (object_literal / entidade ausente no grafo)."""
    if not ref:
        return None
    r = ref.strip()
    if not r:
        return None
    if r in by_native or r in by_name:
        return by_native.get(r) or by_name.get(r)
    key = _deburr(r)
    return by_name.get(key)

# ─────────────────────────────────────────────────────────────────────────────
# Persistência: edges (resolvido) × extraction_candidates (cauda aberta)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_tables() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                create table if not exists {SCHEMA}.extraction_candidates (
                    id          bigint generated always as identity primary key,
                    subject_id  text not null,
                    predicate   text not null,
                    object_ref  text,
                    object_literal text,
                    evidence    text not null default '',
                    source_hash text not null default '',
                    model       text not null default '',
                    created_at  timestamptz not null default now()
                )
            """)


def _insert_triples(triples: list[dict[str, Any]], *, subject_id: str,
                    by_native: dict[str, str], by_name: dict[str, str],
                    source_hash: str, model: str) -> tuple[int, int]:
    """Grava triplas: object_ref resolvido → `edges`; senão → candidates.

    Retorna (n_edges, n_candidates)."""
    n_edges = n_candidates = 0
    with connect() as conn:
        with conn.cursor() as cur:
            for t in triples:
                predicate = t["predicate"]
                ref = t.get("object_ref")
                resolved = resolve_object_ref(ref, by_native, by_name)
                if resolved and resolved != subject_id:
                    cur.execute(
                        f"""insert into {SCHEMA}.edges
                            (source_id, target_id, type, weight, properties, source)
                            values (%s, %s, %s, %s, %s, %s)
                            on conflict (source_id, target_id, type) do nothing""",
                        (subject_id, resolved, predicate, 1.0,
                         json.dumps({"evidence": t.get("evidence", ""), "fase2": True}),
                         "fase2_llm"),
                    )
                    n_edges += 1
                else:
                    cur.execute(
                        f"""insert into {SCHEMA}.extraction_candidates
                            (subject_id, predicate, object_ref, object_literal, evidence, source_hash, model)
                            values (%s, %s, %s, %s, %s, %s, %s)""",
                        (subject_id, predicate, t.get("object_ref") or None,
                         t.get("object_literal") or None, t.get("evidence") or "",
                         source_hash, model),
                    )
                    n_candidates += 1
    return n_edges, n_candidates


def promote(*, gate: int = EVIDENCE_GATE) -> dict[str, Any]:
    """Gate de evidência: promove predicados novos a `core=true`.

    Para cada predicado presente nas arestas fase2 e/ou candidates, conta os
    subjects distintos (evidências independentes). Com ≥ `gate` subjects
    distintos, o predicado entra/sobe a `core=true` em `kg_spike.predicates`
    (o vocabulário que a estrutura-consciente percorre). Retorna contadores."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                select predicate, count(distinct subject_id) as n
                from {SCHEMA}.extraction_candidates
                group by predicate
            """)
            counts = dict(cur.fetchall())
            # Evidência independente = subject distinto (não aresta): um mesmo
            # subject com N arestas do mesmo predicado conta 1.
            cur.execute(f"""
                select type, count(distinct source_id) as n
                from {SCHEMA}.edges where source='fase2_llm'
                group by type
            """)
            for etype, n in cur.fetchall():
                counts[etype] = (counts.get(etype) or 0) + n

    promoted: list[str] = []
    with connect() as conn:
        with conn.cursor() as cur:
            for predicate, n in sorted(counts.items()):
                if n < gate:
                    continue
                cur.execute(
                    f"""insert into {SCHEMA}.predicates (predicate, category, core, description)
                        values (%s, 'Relação', true, %s)
                        on conflict (predicate) do update set core = true""",
                    (predicate, f"promovido pelo gate de evidência (≥{gate} subjects)"),
                )
                promoted.append(predicate)
    logger.info("kg_spike gate: %d predicados promovidos a core=true", len(promoted))
    return {"promoted": promoted, "n_promoted": len(promoted), "gate": gate}


def extract_all(*, limit: int | None = None) -> dict[str, int]:
    """Extrai triplas para todos os editais e grava no schema `kg_spike`.

    `object_ref` resolvido → `kg_spike.edges` (source='fase2_llm'); o resto
    (literal / não-resolvido) → `extraction_candidates` (cauda aberta). O gate
    de evidência roda em `promote()` — separado para decisão explícita.

    Retorna contadores para log/diagnóstico.
    """
    from radar.core.llm.llm_client import make_client

    graph_store.init_schema()
    _ensure_tables()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = make_client(api_key=os.environ.get("OPENAI_API_KEY", ""), max_retries=3)

    with connect() as conn:
        with conn.cursor() as cur:
            by_native, by_name = _load_resolvers(cur)

    todo: list[tuple[str, str]] = []
    for src in EDITAL_SOURCES:
        d = STRUCTURED_DIR / src
        if d.exists():
            todo += [(src, f.stem) for f in sorted(d.glob("*.jsonl"))]
    if limit:
        todo = todo[:limit]

    n_candidates = 0
    n_edges = 0
    n_skipped = 0
    for src, stem in todo:
        subject_id = f"edital:{src}:{stem}"
        silver, _src_hash = _read_silver_text(src, stem)
        native_id = f"{src}:{stem}"
        with connect() as conn:
            with conn.cursor() as cur:
                gold_text = _load_gold_text(cur, native_id)
        text = f"{silver}\n{gold_text}".strip()
        if not text:
            n_skipped += 1
            continue
        # Idempotência por hash (texto + modelo)
        if _hash_key(text, model) == _existing_hash(subject_id):
            n_skipped += 1
            continue

        triples = _extract_llm(client, model, subject_id, text)
        if not triples:
            continue
        e, c = _insert_triples(
            triples, subject_id=subject_id,
            by_native=by_native, by_name=by_name,
            source_hash=_hash_key(text, model), model=model,
        )
        n_edges += e
        n_candidates += c
    logger.info(
        "kg_spike extractor: %d arestas, %d candidatos, %d pulados",
        n_edges, n_candidates, n_skipped,
    )
    return {"edges": n_edges, "candidates": n_candidates, "skipped": n_skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fase 2 — extração LLM de relações semânticas")
    parser.add_argument("--limit", type=int, default=None, help="limita o nº de editais")
    parser.add_argument("--promote-only", action="store_true",
                        help="roda apenas o gate de evidência (não extrai)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    from radar.core.environment import assert_database_target, load_environment_profile

    load_environment_profile()
    assert_database_target("kg_spike extractor")
    if args.promote_only:
        print(promote())
    else:
        print(extract_all(limit=args.limit))
