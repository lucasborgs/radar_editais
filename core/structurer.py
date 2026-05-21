"""
Structurer — camada silver (WIKI.md §11), L2 do stack §12.

Uma passada LLM por unidade (página) do Documento Canônico → blocos
estruturados (texto verbatim + section_path + kind). Artefato neutro e burro,
consumido pela Knowledge gold (síntese wiki) e pela Retrieval gold
(chunkeamento RAG), com caches independentes (§11.4).

Schema, prompt e parâmetros vêm de WIKI.md §11 via core.wiki_schema —
mudança de regra é no doc, não aqui.

Agnóstico à fonte (§12): consome Documento Canônico (§12.3). Não abre
arquivo, não conhece PDF/HTML/API.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import SILVER_DIR
from core import wiki_schema
from pipeline.adapters.base import CanonicalDoc

logger = logging.getLogger(__name__)

_KINDS = set(wiki_schema.structured_doc_schema().get("kinds", []))
_JSON_FENCE_RE = re.compile(r"```(?:json)?|```")


# =============================================================================
# CLIENTE LLM (mesmo contrato de kg_match_service / etl_process)
# =============================================================================

def _make_client():
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    from core.llm_client import make_client

    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return make_client(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"
    if backend == "ollama":
        return make_client(api_key="ollama", base_url="http://localhost:11434/v1"), \
            os.getenv("OLLAMA_MODEL", "llama3.2")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return make_client(api_key=api_key), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# =============================================================================
# PASSADA LLM POR PÁGINA
# =============================================================================

def _parse_blocks(raw: str) -> list[dict]:
    """Extrai o array JSON de blocos da resposta do LLM. Tolerante a fence."""
    s = _JSON_FENCE_RE.sub("", raw).strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", s, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    return data if isinstance(data, list) else []


def structure_page(
    client,
    model: str,
    doc_name: str,
    page_num: int,
    page_text: str,
    carry_section_path: list[str],
) -> list[dict]:
    """Uma chamada LLM → blocos crus desta página (sem idx/doc/page ainda)."""
    if not page_text.strip():
        return []
    params = wiki_schema.structurer_params()
    prompt = wiki_schema.structurer_prompt().format(
        doc_name=doc_name,
        page_num=page_num,
        page_text=page_text,
        carry_section_path=json.dumps(carry_section_path, ensure_ascii=False),
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=params.get("temperature", 0.0),
        max_tokens=params.get("max_tokens", 4000),
    )
    blocks = _parse_blocks(resp.choices[0].message.content or "")
    clean: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        text = (b.get("text") or "").strip()
        if not text:
            continue
        kind = b.get("kind") if b.get("kind") in _KINDS else "paragraph"
        sp = b.get("section_path")
        clean.append({
            "section_path": sp if isinstance(sp, list) else [],
            "kind": kind,
            "text": text,
        })
    return clean


def _structure_single_doc(
    client, model: str, doc: dict
) -> list[dict]:
    """Estrutura um documento — páginas serial (carry_section_path entre
    páginas cria dependência sequencial; paralelizar dentro do doc quebraria
    a continuidade de seção)."""
    out: list[dict] = []
    carry: list[str] = []
    doc_name = doc["doc_name"]
    for page_num, page_text in enumerate(doc["units"], start=1):
        page_blocks = structure_page(
            client, model, doc_name, page_num, page_text, carry
        )
        for b in page_blocks:
            out.append({
                "doc": doc_name,
                "page": page_num,
                "section_path": b["section_path"],
                "kind": b["kind"],
                "text": b["text"],
            })
        for b in reversed(page_blocks):
            if b["section_path"]:
                carry = b["section_path"]
                break
    return out


def structure_document(documents: CanonicalDoc) -> list[dict]:
    """Estrutura todos os documentos do edital. Docs rodam em paralelo
    (`max_concurrent_docs` §11.2); páginas dentro de cada doc serial (carry).
    Retorna blocos com idx/doc/page globais, em ordem do `documents` input.
    Consome Documento Canônico (§12.3) — agnóstico à fonte.
    """
    client, model = _make_client()
    if not documents:
        return []

    max_workers = wiki_schema.structurer_params().get("max_concurrent_docs", 8)
    workers = max(1, min(max_workers, len(documents)))
    # OpenAI/Gemini clients são thread-safe; LLM calls são I/O-bound (GIL libera).
    with ThreadPoolExecutor(max_workers=workers) as ex:
        per_doc = list(ex.map(
            lambda d: _structure_single_doc(client, model, d), documents
        ))

    # Re-monta na ordem original de `documents`; idx global determinístico.
    blocks: list[dict] = []
    idx = 0
    for doc_blocks in per_doc:
        for b in doc_blocks:
            blocks.append({"idx": idx, **b})
            idx += 1
    return blocks


# =============================================================================
# ARTEFATO SILVER + CACHE (§11.4)
# =============================================================================

def _silver_paths(source: str, edital_id: str) -> tuple[Path, Path]:
    base = SILVER_DIR / "structured_docs" / source
    return base / f"{edital_id}.jsonl", base / f"{edital_id}.meta.json"


def _source_hash(documents: CanonicalDoc) -> str:
    """Hash do Documento Canônico — sem I/O, determinístico a partir do
    contrato §12.3. Mudança vs hash antigo (re-extraía de Path): invalida
    cache silver uma vez no upgrade — esperado."""
    h = hashlib.md5()
    for doc in sorted(documents, key=lambda d: d["doc_name"]):
        h.update(doc["doc_name"].encode())
        for unit in doc["units"]:
            h.update(unit.encode("utf-8", "ignore"))
    return h.hexdigest()


def _cache_meta() -> dict:
    p = wiki_schema.structurer_params()
    return {
        "silver_version": p.get("silver_version", "1"),
        "prompt_version": p.get("prompt_version", "1"),
    }


def build_or_load_structured_doc(
    source: str, edital_id: str, documents: CanonicalDoc, force: bool = False
) -> list[dict]:
    """Entry point cacheado. Regenera só se conteúdo mudou ou versão bumpou
    (§11.4). Retorna a lista de blocos. Lista vazia = sem conteúdo/falha →
    o caller decide o fallback (comportamento atual preservado).
    """
    jsonl_path, meta_path = _silver_paths(source, edital_id)
    src_hash = _source_hash(documents) if documents else ""
    want_meta = {**_cache_meta(), "source_hash": src_hash}

    if not force and jsonl_path.exists() and meta_path.exists():
        try:
            have = json.loads(meta_path.read_text(encoding="utf-8"))
            if all(have.get(k) == want_meta[k] for k in want_meta):
                return [
                    json.loads(line)
                    for line in jsonl_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
        except Exception as e:
            logger.warning("structurer: cache inválido p/ %s: %s", edital_id, e)

    if not documents:
        return []

    blocks = structure_document(documents)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.write_text(
        "\n".join(json.dumps(b, ensure_ascii=False) for b in blocks),
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps({**want_meta, "n_blocks": len(blocks)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "structurer: edital=%s → %d blocos (silver_v%s)",
        edital_id, len(blocks), want_meta["silver_version"],
    )
    return blocks
