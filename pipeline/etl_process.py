"""
ETL Process — Knowledge gold (L3a, WIKI.md §12).

A LLM lê o Documento Canônico (via Source Adapter L1) já estruturado pela
camada silver (L2) e produz a wiki page → knowledge_graph/wiki/{id}.json.

Fase 4: deixou de re-ler PDFs crus + truncar — agora consome blocos silver
(boilerplate/signature filtrados, texto limpo). O LLM da síntese recebe
texto de maior sinal por token, e a leitura LLM dos PDFs é amortizada com
a Retrieval gold (a mesma silver alimenta os dois).

Cache do etl_process: invalida quando o silver muda (silver_version,
prompt_version, source_hash) OU quando o metadata muda. §11.4.

Uso:
    python pipeline/etl_process.py
    python pipeline/etl_process.py --backend openai
    python pipeline/etl_process.py --edital 782 790
    python pipeline/etl_process.py --skip-cache
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime

from config import KG_WIKI_DIR, KNOWLEDGE_GRAPH_DIR
from core.kg import kg_store, wiki_schema
from core.kg.edital_id import source_of, wiki_page_path
from core.structurer import build_or_load_structured_doc
from pipeline.adapters.base import get_adapter

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

# Schema autoritativo em WIKI.md + wikis/<source>.md. A fonte de cada edital
# é inferida do seu `edital_id` prefixado (§12 — pós-Épico B/C). Pra processar
# uma fonte específica, passe `--source` na CLI ou `source=` em main().

INDEX_FILE           = KNOWLEDGE_GRAPH_DIR / "index.json"
INDEX_HISTORICO_FILE = KNOWLEDGE_GRAPH_DIR / "index_historico.json"
CACHE_FILE           = KG_WIKI_DIR / ".etl_process_cache.json"


# =============================================================================
# UTILITÁRIOS
# =============================================================================

_DROP_KINDS_FOR_SYNTHESIS = {"boilerplate", "signature"}


def _load_documents_via_silver(edital_id: str) -> tuple[list[tuple[str, str]], dict]:
    """Carrega o conteúdo do edital via Source Adapter (L1) + silver (L2).

    `edital_id` chega prefixado (`finep:782`). Adapter e structurer trabalham
    em escopo de fonte específica → recebem o native_id. Source vem do prefixo.

    Retorna `(documents, silver_meta)`. Vazio = sem conteúdo extraível;
    caller decide o fallback (`_save_minimal_wiki_page`).
    """
    from core.kg.edital_id import native_id_of, source_of  # noqa: PLC0415

    source = source_of(edital_id)
    native = native_id_of(edital_id)

    adapter = get_adapter(source)
    canonical = adapter.to_documents(native)
    if not canonical:
        return [], {}

    blocks = build_or_load_structured_doc(source, native, canonical)
    if not blocks:
        return [], {}

    by_doc: dict[str, list[str]] = {}
    for b in blocks:
        if b.get("kind") in _DROP_KINDS_FOR_SYNTHESIS:
            continue
        text = (b.get("text") or "").strip()
        if not text:
            continue
        by_doc.setdefault(b["doc"], []).append(text)
    documents = [(name, "\n\n".join(parts)) for name, parts in by_doc.items()]
    documents.sort(key=lambda x: x[0])

    # silver_meta vem do sidecar gravado pelo structurer (§11.4).
    from core.structurer import _silver_paths  # noqa: PLC0415 — split-helper interno
    _, meta_path = _silver_paths(source, native)
    silver_meta: dict = {}
    if meta_path.exists():
        try:
            silver_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return documents, silver_meta


def _fit_to_budget(pdfs: list[tuple[str, str]], model: str) -> list[tuple[str, str]]:
    """
    Distribui o budget de caracteres entre os PDFs.
    PDFs menores entram completos primeiro; os maiores preenchem o espaço restante.
    """
    budgets = wiki_schema.llm_params().get("model_char_budgets", {})
    budget = budgets.get(model, 80_000 * 4)
    total_chars = sum(len(t) for _, t in pdfs)
    if total_chars <= budget:
        return pdfs

    pdfs_sorted = sorted(pdfs, key=lambda x: len(x[1]))
    remaining = budget
    result = []
    for name, text in pdfs_sorted:
        result.append((name, text[:remaining]))
        remaining -= len(text)
        if remaining <= 0:
            break
    return result


def _content_hash(metadata: dict, silver_meta: dict) -> str:
    """Cache do etl_process invalida quando o silver muda (silver_version /
    prompt_version / source_hash) OU quando o metadata muda. Mais barato e
    semanticamente mais correto que hashear o texto inteiro dos PDFs."""
    payload = json.dumps(metadata, sort_keys=True) + json.dumps(silver_meta, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    KG_WIKI_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


# =============================================================================
# LLM
# =============================================================================

def _make_client(backend: str):
    from openai import OpenAI
    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or \
                  getpass.getpass("Gemini API Key: ")
        return OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"
    api_key = os.getenv("OPENAI_API_KEY") or getpass.getpass("OpenAI API Key: ")
    return OpenAI(api_key=api_key), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _call_llm(client, model: str, metadata: dict, pdfs: list[tuple[str, str]]) -> dict:
    """Síntese da wiki page. Source vem do edital_id prefixado do metadata."""
    source = source_of(metadata["id"])
    metadata_keys = wiki_schema.metadata_to_llm_keys(source)
    metadata_str = json.dumps(
        {k: metadata.get(k, [] if k in ("themes", "publico_alvo", "fonte_recurso") else "") for k in metadata_keys},
        ensure_ascii=False, indent=2,
    )

    fitted_pdfs = _fit_to_budget(pdfs, model)
    docs_parts = [f"### {name}\n{text}" for name, text in fitted_pdfs]
    documents_str = "\n\n".join(docs_parts) if docs_parts else "(sem documentos PDF disponíveis)"

    prompt = wiki_schema.extraction_prompt(source).format(metadata=metadata_str, documents=documents_str)

    llm_params = wiki_schema.llm_params()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=llm_params.get("temperature", 0.1),
        max_tokens=llm_params.get("max_tokens", 1500),
    )
    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    return json.loads(raw)


# =============================================================================
# SAÍDA
# =============================================================================

# Promoção dos campos de match (wiki page → índice durável) — single source em
# core/wiki_schema (compartilhado com build_knowledge_graph, que os carrega adiante).
from core.kg.wiki_schema import promote_match_fields as _promote_match_fields  # noqa: E402


def _save_wiki_page(entry: dict, synthesized: dict) -> dict:
    wiki_page = {
        "id":            entry["id"],
        "title":         entry["title"],
        "status":        entry["status"],
        "deadline":      entry["deadline"],
        "pub_date":      entry.get("pub_date", ""),
        "pub_year":      entry.get("pub_year", "desconhecido"),
        "link":          entry["link"],
        "themes":        entry.get("themes", []),
        "publico_alvo":  entry.get("publico_alvo", []),
        "fonte_recurso": entry.get("fonte_recurso", []),
        "objective":               synthesized.get("objective"),
        "mechanism":               synthesized.get("mechanism"),
        "eligible_entities":       synthesized.get("eligible_entities", entry.get("publico_alvo", [])),
        "value_range":             synthesized.get("value_range", {"min_brl": None, "max_brl": None}),
        "trl_range":               synthesized.get("trl_range", {"min": None, "max": None}),
        "required_certifications": synthesized.get("required_certifications", []),
        "counterpart_required":    synthesized.get("counterpart_required", False),
        "key_requirements":        synthesized.get("key_requirements", []),
        "key_facts":               synthesized.get("key_facts", []),
        "proposal_sections":       synthesized.get("proposal_sections", []),
        "generated_at":            datetime.now().strftime("%Y-%m-%d"),
        "source":                  "etl_process",
    }
    page_path = wiki_page_path(entry["id"])
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(
        json.dumps(wiki_page, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return wiki_page


def _save_minimal_wiki_page(entry: dict) -> dict:
    """Wiki page mínima para editais sem PDFs disponíveis (sem chamada LLM)."""
    wiki_page = {
        "id":            entry["id"],
        "title":         entry["title"],
        "status":        entry["status"],
        "deadline":      entry["deadline"],
        "pub_date":      entry.get("pub_date", ""),
        "pub_year":      entry.get("pub_year", "desconhecido"),
        "link":          entry["link"],
        "themes":        entry.get("themes", []),
        "publico_alvo":  entry.get("publico_alvo", []),
        "fonte_recurso": entry.get("fonte_recurso", []),
        "objective":               None,
        "mechanism":               None,
        "eligible_entities":       entry.get("publico_alvo", []),
        "value_range":             {"min_brl": None, "max_brl": None},
        "trl_range":               {"min": None, "max": None},
        "required_certifications": [],
        "counterpart_required":    False,
        "key_requirements":        [],
        "key_facts":               [],
        "proposal_sections":       [],
        "generated_at":            datetime.now().strftime("%Y-%m-%d"),
        "source":                  "metadata_only",
    }
    page_path = wiki_page_path(entry["id"])
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(
        json.dumps(wiki_page, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return wiki_page


# =============================================================================
# MAIN
# =============================================================================

def main(
    backend: str = "gemini",
    source: str | None = None,
    edital_ids: list[str] | None = None,
    skip_cache: bool = False,
    delay: float = 1.5,
    historico: bool = False,
) -> None:
    """Processa editais do índice → wiki pages.

    `source`: se fornecido, processa só editais dessa fonte (`finep`, `fapesp`, …).
              None = processa todas as fontes presentes no índice (multi-fonte).
    """
    print("=" * 60)
    print("ETL PROCESS — wiki page por edital")
    print("=" * 60)

    index_path = INDEX_HISTORICO_FILE if historico else INDEX_FILE
    if not index_path.exists():
        print(f"ERRO: {index_path} não encontrado. Execute build_knowledge_graph primeiro.")
        return

    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = index.get("editais", [])

    if source:
        entries = [e for e in entries if source_of(e["id"]) == source]
        print(f"Filtro: source={source} → {len(entries)} entries")

    if historico:
        # Processa apenas encerrados (vigentes já foram processados)
        entries = [e for e in entries if not wiki_page_path(e["id"]).exists()]
        print(f"Modo histórico — editais sem wiki page: {len(entries)}")

    if edital_ids:
        entries = [e for e in entries if e["id"] in edital_ids]

    print(f"Editais: {len(entries)}")

    # Cliente LLM da síntese — só constrói se ao menos um edital tem
    # silver disponível (caso contrário tudo cai no minimal).
    client, model = _make_client(backend)
    print(f"Backend: {backend} | Model: {model}")

    cache = {} if skip_cache else _load_cache()
    processed = skipped = minimal = errors = 0
    built_pages: dict[str, dict] = {}  # {edital_id: wiki_page} p/ persistir no blob durável

    for i, entry in enumerate(entries, 1):
        eid = entry["id"]
        # L1+L2: documento canônico → silver (cache hit se já indexado p/ B).
        documents, silver_meta = _load_documents_via_silver(eid)
        content_hash = _content_hash(entry, silver_meta)

        if not skip_cache and cache.get(eid) == content_hash \
                and wiki_page_path(eid).exists():
            logger.info("[%d/%d] %s — cache hit", i, len(entries), eid)
            # Mesmo no cache hit, lê a wiki page existente para (a) promover os
            # campos de match ao índice e (b) entrar no blob durável — garante que
            # o Postgres carregue a página mesmo quando a síntese não re-roda.
            try:
                wp = json.loads(wiki_page_path(eid).read_text(encoding="utf-8"))
                _promote_match_fields(entry, wp)
                built_pages[eid] = wp
            except Exception:
                pass
            skipped += 1
            continue

        logger.info("[%d/%d] %s — %d docs (silver)", i, len(entries), eid, len(documents))

        if not documents:
            wp = _save_minimal_wiki_page(entry)
            _promote_match_fields(entry, wp)
            built_pages[eid] = wp
            minimal += 1
        else:
            try:
                result = _call_llm(client, model, entry, documents)
                wp = _save_wiki_page(entry, result)
                _promote_match_fields(entry, wp)
                built_pages[eid] = wp
                time.sleep(delay)
                processed += 1
            except Exception as e:
                logger.error("Erro em %s: %s — salvando wiki page mínima", eid, e)
                wp = _save_minimal_wiki_page(entry)
                _promote_match_fields(entry, wp)
                built_pages[eid] = wp
                errors += 1
                continue

        cache[eid] = content_hash
        _save_cache(cache)

    # Persiste o índice ENRIQUECIDO (campos de match promovidos das wiki pages)
    # no store durável. kg_store.save grava o arquivo local SEMPRE + upsert no
    # Postgres se configurado → em cloud o HybridMatch (que lê o índice do Postgres)
    # passa a enxergar mechanism/trl/contrapartida/elegibilidade mesmo sem a wiki
    # page (arquivo) no container web. Sem este save, fecha-se só metade do laço.
    if entries:
        kg_store.save("index_historico" if historico else "index", index)
        print(f"Índice enriquecido persistido ({len(entries)} entradas, campos de match promovidos)")

    # Tier 2: persiste as wiki pages CHEIAS no blob durável (Postgres), p/ os
    # consumidores (checklist/compliance/brief/KGMatch/HybridMatch) lerem em prod,
    # onde o arquivo por-edital não existe. MERGE — não apaga páginas fora deste run.
    if built_pages:
        kg_store.save_wiki_pages(built_pages)
        print(f"Wiki pages persistidas no store durável: {len(built_pages)}")

    print(f"\n{'=' * 60}")
    print(f"RESUMO: {processed} LLM, {minimal} mínimas, {skipped} cache, {errors} erros")
    print(f"Wiki pages: {KG_WIKI_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETL Process — wiki page por edital")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "openai"])
    parser.add_argument("--source", default=None,
                        help="Slug da fonte (finep, fapesp, …). Omitido = todas")
    parser.add_argument("--edital", nargs="+", dest="edital_ids",
                        help="IDs específicos (já prefixados, ex.: finep:782)")
    parser.add_argument("--skip-cache", action="store_true", help="Ignora cache e reprocessa tudo")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay entre chamadas LLM (s)")
    parser.add_argument("--historico", action="store_true", help="Processa editais históricos (encerrados)")
    args = parser.parse_args()

    main(
        backend=args.backend,
        source=args.source,
        edital_ids=args.edital_ids,
        skip_cache=args.skip_cache,
        delay=args.delay,
        historico=args.historico,
    )
