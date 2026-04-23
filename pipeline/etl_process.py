"""
ETL Process — geração da wiki page por edital.

A LLM lê todos os PDFs do edital + metadados HTML em uma única chamada e produz
a wiki page estruturada → knowledge_graph/wiki/{id}.json

A wiki page é o único artefato gerado. Os PDFs brutos permanecem como raw sources
e são lidos diretamente pela WritingSession quando o usuário seleciona um edital.

Cache por hash MD5 do conteúdo de todos os PDFs + metadados do index.json.
Só reprocessa se algum documento mudar (retificação, aditivo).

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
from pathlib import Path

from config import BRONZE_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR, KG_WIKI_DIR
from core import wiki_schema

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

# Schema autoritativo em WIKI.md + wikis/finep.md (ver core.wiki_schema)
_SOURCE = "finep"

INDEX_FILE           = KNOWLEDGE_GRAPH_DIR / "index.json"
INDEX_HISTORICO_FILE = KNOWLEDGE_GRAPH_DIR / "index_historico.json"
CACHE_FILE           = KG_WIKI_DIR / ".etl_process_cache.json"


# =============================================================================
# UTILITÁRIOS
# =============================================================================

def _extract_pdf_text(pdf_path: Path) -> str:
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as e:
        logger.warning("Erro ao extrair %s: %s", pdf_path.name, e)
        return ""


def _should_skip(name: str) -> bool:
    return any(kw in name.lower() for kw in wiki_schema.skip_keywords(_SOURCE))


def _load_pdfs(edital_id: str) -> list[tuple[str, str]]:
    """Retorna lista de (nome, texto) para os PDFs relevantes do edital.

    Tenta primeiro os PDFs físicos em FINEP_PDFS_DIR; se o diretório não
    existir, cai no fallback de pdf_texts embutido no JSON bronze.
    """
    pdf_dir = FINEP_PDFS_DIR / edital_id
    if pdf_dir.exists():
        result = []
        for pdf in sorted(pdf_dir.glob("*.pdf")):
            if not _should_skip(pdf.stem):
                text = _extract_pdf_text(pdf)
                if text.strip():
                    result.append((pdf.stem, text))
        return result

    return _load_pdfs_from_bronze(edital_id)


def _load_pdfs_from_bronze(edital_id: str) -> list[tuple[str, str]]:
    """Fallback: lê pdf_texts do JSON bronze mais recente."""
    raw_dir = BRONZE_DIR / "finep_raw"
    if not raw_dir.exists():
        return []

    # Arquivo bronze mais recente
    bronze_files = sorted(raw_dir.glob("*.json"), reverse=True)
    for bf in bronze_files:
        try:
            chamadas = json.loads(bf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ch in chamadas:
            cid = str(ch.get("chamada_id", ""))
            if cid != edital_id:
                continue
            pdf_texts = ch.get("pdf_texts") or {}
            result = [
                (nome, texto)
                for nome, texto in pdf_texts.items()
                if texto and not _should_skip(nome)
            ]
            return sorted(result, key=lambda x: x[0])
    return []


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


def _content_hash(metadata: dict, pdfs: list[tuple[str, str]]) -> str:
    payload = json.dumps(metadata, sort_keys=True) + "".join(t for _, t in pdfs)
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
    metadata_keys = wiki_schema.metadata_to_llm_keys(_SOURCE)
    metadata_str = json.dumps(
        {k: metadata.get(k, [] if k in ("themes", "publico_alvo", "fonte_recurso") else "") for k in metadata_keys},
        ensure_ascii=False, indent=2,
    )

    fitted_pdfs = _fit_to_budget(pdfs, model)
    docs_parts = [f"### {name}\n{text}" for name, text in fitted_pdfs]
    documents_str = "\n\n".join(docs_parts) if docs_parts else "(sem documentos PDF disponíveis)"

    prompt = wiki_schema.extraction_prompt(_SOURCE).format(metadata=metadata_str, documents=documents_str)

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

def _save_wiki_page(entry: dict, synthesized: dict) -> None:
    wiki_page = {
        "id":            entry["id"],
        "title":         entry["title"],
        "status":        entry["status"],
        "deadline":      entry["deadline"],
        "pub_date":      entry.get("pub_date", ""),
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
    KG_WIKI_DIR.mkdir(parents=True, exist_ok=True)
    (KG_WIKI_DIR / f"{entry['id']}.json").write_text(
        json.dumps(wiki_page, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _save_minimal_wiki_page(entry: dict) -> None:
    """Wiki page mínima para editais sem PDFs disponíveis (sem chamada LLM)."""
    wiki_page = {
        "id":            entry["id"],
        "title":         entry["title"],
        "status":        entry["status"],
        "deadline":      entry["deadline"],
        "pub_date":      entry.get("pub_date", ""),
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
    KG_WIKI_DIR.mkdir(parents=True, exist_ok=True)
    (KG_WIKI_DIR / f"{entry['id']}.json").write_text(
        json.dumps(wiki_page, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# =============================================================================
# MAIN
# =============================================================================

def main(
    backend: str = "gemini",
    edital_ids: list[str] | None = None,
    skip_cache: bool = False,
    delay: float = 1.5,
    historico: bool = False,
) -> None:
    print("=" * 60)
    print("ETL PROCESS — wiki page por edital")
    print("=" * 60)

    index_path = INDEX_HISTORICO_FILE if historico else INDEX_FILE
    if not index_path.exists():
        print(f"ERRO: {index_path} não encontrado. Execute build_knowledge_graph primeiro.")
        return

    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = index.get("editais", [])

    if historico:
        # Processa apenas encerrados (vigentes já foram processados)
        wiki_dir = KG_WIKI_DIR
        entries = [e for e in entries if not (wiki_dir / f"{e['id']}.json").exists()]
        print(f"Modo histórico — editais sem wiki page: {len(entries)}")

    if edital_ids:
        entries = [e for e in entries if e["id"] in edital_ids]

    print(f"Editais: {len(entries)}")

    needs_llm = any(_load_pdfs(e["id"]) for e in entries)
    client, model = (None, "") if not needs_llm else _make_client(backend)
    if needs_llm:
        print(f"Backend: {backend} | Model: {model}")

    cache = {} if skip_cache else _load_cache()
    processed = skipped = minimal = errors = 0

    for i, entry in enumerate(entries, 1):
        eid = entry["id"]
        pdfs = _load_pdfs(eid)
        content_hash = _content_hash(entry, pdfs)

        if not skip_cache and cache.get(eid) == content_hash \
                and (KG_WIKI_DIR / f"{eid}.json").exists():
            logger.info("[%d/%d] %s — cache hit", i, len(entries), eid)
            skipped += 1
            continue

        logger.info("[%d/%d] %s — %d PDFs", i, len(entries), eid, len(pdfs))

        if not pdfs or client is None:
            _save_minimal_wiki_page(entry)
            minimal += 1
        else:
            try:
                result = _call_llm(client, model, entry, pdfs)
                _save_wiki_page(entry, result)
                time.sleep(delay)
                processed += 1
            except Exception as e:
                logger.error("Erro em %s: %s — salvando wiki page mínima", eid, e)
                _save_minimal_wiki_page(entry)
                errors += 1
                continue

        cache[eid] = content_hash
        _save_cache(cache)

    print(f"\n{'=' * 60}")
    print(f"RESUMO: {processed} LLM, {minimal} mínimas, {skipped} cache, {errors} erros")
    print(f"Wiki pages: {KG_WIKI_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETL Process — wiki page por edital")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "openai"])
    parser.add_argument("--edital", nargs="+", dest="edital_ids", help="IDs específicos")
    parser.add_argument("--skip-cache", action="store_true", help="Ignora cache e reprocessa tudo")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay entre chamadas LLM (s)")
    parser.add_argument("--historico", action="store_true", help="Processa editais históricos (encerrados)")
    args = parser.parse_args()

    main(
        backend=args.backend,
        edital_ids=args.edital_ids,
        skip_cache=args.skip_cache,
        delay=args.delay,
        historico=args.historico,
    )
