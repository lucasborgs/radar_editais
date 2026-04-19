"""
ETL Process — geração de card + section index por edital.

Substitui etl_finep_facts.py + etl_finep_cards.py.

A LLM lê todos os PDFs do edital + metadados HTML em uma única chamada e produz:
  - card estruturado  → knowledge_graph/cards/{id}.json       (matching Stage 1)
  - section index     → silver_data/section_index/{id}.json   (WritingSession)

O section index preserva o texto original dos PDFs organizado por seção —
sem síntese, sem reformulação. O card é a única camada de interpretação.

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

from config import (
    FINEP_PDFS_DIR,
    KNOWLEDGE_GRAPH_DIR,
    KG_CARDS_DIR,
    SECTION_INDEX_DIR,
)

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

INDEX_FILE  = KNOWLEDGE_GRAPH_DIR / "index.json"
CACHE_FILE  = KG_CARDS_DIR / ".etl_process_cache.json"

# PDFs a ignorar (documentos auxiliares sem conteúdo normativo útil)
_SKIP_KEYWORDS = [
    "minuta", "declaracao", "carta_de_manifestacao", "faq",
    "apresentacao", "resultado", "oficio", "telas_fap",
    "orientacoes_para_apresentacao", "tabela_com_requisitos",
    "orientacoes_para_despesas", "relatorio_parcial",
]


# =============================================================================
# PROMPT
# =============================================================================

_PROMPT = """Você é um especialista em editais de fomento à inovação no Brasil.

Leia os documentos abaixo de uma chamada pública e produza dois artefatos em JSON.

---
METADADOS (do portal web):
{metadata}

---
DOCUMENTOS (PDFs da chamada):
{documents}

---
Produza APENAS o JSON abaixo, sem markdown ou texto extra:

{{
  "card": {{
    "objective": "síntese em 2-3 frases do que este edital financia e para quem",
    "mechanism": "subvencao|reembolsavel|investimento|misto|null",
    "eligible_entities": ["empresas", "startups", "ICTs"],
    "eligible_sectors": ["setor1", "setor2"],
    "value_range": {{"min_brl": null, "max_brl": null}},
    "trl_range": {{"min": null, "max": null}},
    "required_certifications": [],
    "counterpart_required": false,
    "key_requirements": ["máx 5 requisitos concretos e objetivos"],
    "key_facts": ["5 fatos mais relevantes para uma empresa decidir se candidatar"]
  }},
  "sections": [
    {{
      "title": "título da seção",
      "content": "texto original desta seção, sem reformulação ou síntese"
    }}
  ]
}}

Regras para sections:
- Identifique as seções lógicas dos documentos (objeto, elegibilidade, recursos, cronograma, etc.)
- Preserve o texto original — não sintetize nem reformule
- Inclua apenas seções com conteúdo normativo útil (ignore capas, sumários, bases legais puras)
- Títulos das seções em português, claros e curtos

Regras para card:
- mechanism: classifique pelo mecanismo financeiro principal
- trl_range: inteiros 1-9, null se não mencionado
- value_range: valores em reais como inteiros, null se não mencionado
- key_requirements: máx 5 itens, cada um autocontido e verificável
- key_facts: os 5 fatos que uma empresa usaria para decidir se vale candidatar"""


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


def _should_skip(pdf_path: Path) -> bool:
    name = pdf_path.stem.lower()
    return any(kw in name for kw in _SKIP_KEYWORDS)


def _load_pdfs(edital_id: str) -> list[tuple[str, str]]:
    """Retorna lista de (nome_arquivo, texto) para os PDFs do edital."""
    pdf_dir = FINEP_PDFS_DIR / edital_id
    if not pdf_dir.exists():
        return []
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    result = []
    for pdf in pdfs:
        if not _should_skip(pdf):
            text = _extract_pdf_text(pdf)
            if text.strip():
                result.append((pdf.stem, text))
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
    KG_CARDS_DIR.mkdir(parents=True, exist_ok=True)
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
    metadata_str = json.dumps({
        "title":        metadata.get("title", ""),
        "status":       metadata.get("status", ""),
        "deadline":     metadata.get("deadline", ""),
        "themes":       metadata.get("themes", []),
        "publico_alvo": metadata.get("publico_alvo", []),
        "fonte_recurso":metadata.get("fonte_recurso", []),
        "link":         metadata.get("link", ""),
    }, ensure_ascii=False, indent=2)

    docs_parts = []
    for name, text in pdfs:
        docs_parts.append(f"### {name}\n{text[:8000]}")
    documents_str = "\n\n".join(docs_parts) if docs_parts else "(sem documentos PDF disponíveis)"

    prompt = _PROMPT.format(metadata=metadata_str, documents=documents_str)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=3000,
    )
    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    return json.loads(raw)


# =============================================================================
# SAÍDA
# =============================================================================

def _save_card(entry: dict, synthesized: dict) -> None:
    card = {
        "id":           entry["id"],
        "title":        entry["title"],
        "status":       entry["status"],
        "deadline":     entry["deadline"],
        "pub_date":     entry.get("pub_date", ""),
        "link":         entry["link"],
        "themes":       entry.get("themes", []),
        "publico_alvo": entry.get("publico_alvo", []),
        "fonte_recurso":entry.get("fonte_recurso", []),
        "objective":    synthesized.get("objective"),
        "mechanism":    synthesized.get("mechanism"),
        "eligible_entities":      synthesized.get("eligible_entities", entry.get("publico_alvo", [])),
        "eligible_sectors":       synthesized.get("eligible_sectors", entry.get("themes", [])),
        "value_range":            synthesized.get("value_range", {"min_brl": None, "max_brl": None}),
        "trl_range":              synthesized.get("trl_range", {"min": None, "max": None}),
        "required_certifications":synthesized.get("required_certifications", []),
        "counterpart_required":   synthesized.get("counterpart_required", False),
        "key_requirements":       synthesized.get("key_requirements", []),
        "key_facts":              synthesized.get("key_facts", []),
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
        "source":       "etl_process",
    }
    KG_CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (KG_CARDS_DIR / f"{entry['id']}.json").write_text(
        json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _save_section_index(entry: dict, sections: list[dict]) -> None:
    payload = {
        "edital_id": entry["id"],
        "title":     entry["title"],
        "source":    "etl_process",
        "sections":  [
            {"title": s["title"], "content": s["content"]}
            for s in sections
            if s.get("title") and s.get("content")
        ],
    }
    SECTION_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    (SECTION_INDEX_DIR / f"{entry['id']}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _save_minimal(entry: dict) -> None:
    """Card mínimo para editais sem PDFs (sem chamada LLM)."""
    card = {
        "id":           entry["id"],
        "title":        entry["title"],
        "status":       entry["status"],
        "deadline":     entry["deadline"],
        "pub_date":     entry.get("pub_date", ""),
        "link":         entry["link"],
        "themes":       entry.get("themes", []),
        "publico_alvo": entry.get("publico_alvo", []),
        "fonte_recurso":entry.get("fonte_recurso", []),
        "objective":    None,
        "mechanism":    None,
        "eligible_entities":      entry.get("publico_alvo", []),
        "eligible_sectors":       entry.get("themes", []),
        "value_range":            {"min_brl": None, "max_brl": None},
        "trl_range":              {"min": None, "max": None},
        "required_certifications":[],
        "counterpart_required":   False,
        "key_requirements":       [],
        "key_facts":              [],
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
        "source":       "metadata_only",
    }
    KG_CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (KG_CARDS_DIR / f"{entry['id']}.json").write_text(
        json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# =============================================================================
# MAIN
# =============================================================================

def main(
    backend: str = "gemini",
    edital_ids: list[str] | None = None,
    skip_cache: bool = False,
    delay: float = 1.5,
) -> None:
    print("=" * 60)
    print("ETL PROCESS — card + section index por edital")
    print("=" * 60)

    if not INDEX_FILE.exists():
        print(f"ERRO: {INDEX_FILE} não encontrado. Execute build_knowledge_graph primeiro.")
        return

    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    entries = index.get("editais", [])

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
        cache_key = eid

        if not skip_cache and cache.get(cache_key) == content_hash \
                and (KG_CARDS_DIR / f"{eid}.json").exists():
            logger.info("[%d/%d] %s — cache hit", i, len(entries), eid)
            skipped += 1
            continue

        logger.info("[%d/%d] %s — %d PDFs", i, len(entries), eid, len(pdfs))

        if not pdfs or client is None:
            _save_minimal(entry)
            minimal += 1
        else:
            try:
                result = _call_llm(client, model, entry, pdfs)
                _save_card(entry, result.get("card", {}))
                _save_section_index(entry, result.get("sections", []))
                time.sleep(delay)
                processed += 1
            except Exception as e:
                logger.error("Erro em %s: %s — salvando card mínimo", eid, e)
                _save_minimal(entry)
                errors += 1
                continue

        cache[cache_key] = content_hash
        _save_cache(cache)

    print(f"\n{'=' * 60}")
    print(f"RESUMO: {processed} LLM, {minimal} mínimos, {skipped} cache, {errors} erros")
    print(f"Cards:    {KG_CARDS_DIR}/")
    print(f"Sections: {SECTION_INDEX_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETL Process — card + sections por edital")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "openai"])
    parser.add_argument("--edital", nargs="+", dest="edital_ids", help="IDs específicos")
    parser.add_argument("--skip-cache", action="store_true", help="Ignora cache e reprocessa tudo")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay entre chamadas LLM (s)")
    args = parser.parse_args()

    main(
        backend=args.backend,
        edital_ids=args.edital_ids,
        skip_cache=args.skip_cache,
        delay=args.delay,
    )
