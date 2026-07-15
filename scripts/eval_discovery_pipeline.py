"""Simulação do pipeline de Discovery completo (Cenário A da spec).

Produz registros no mesmo formato de `discovered_opportunities` e compara
com o método atual (LLM em 6k chars, sem PDFs).

Uso:
  python scripts/eval_discovery_pipeline.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
import requests
import yaml
from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, LLMConfig, LLMExtractionStrategy
from crawl4ai.content_filter_strategy import BM25ContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(SCRIPT_DIR, "eval_discovery_pipeline_results.json")
WIKIS_DIR = Path(__file__).resolve().parent.parent / "wikis"

TEST_URLS = [
    {
        "name": "FAPEMIG - Sede Compete Minas",
        "url": "https://fapemig.br/oportunidades/chamadas-e-editais/fapemig-sede-compete-minas",
        "source": "fapemig",
        "source_type": "discovery",
    },
    {
        "name": "FINEP - Mais Inovação (Mobilidade Sustentável)",
        "url": "https://www.finep.gov.br/e/chamada-publica/222684/755376",
        "source": "finep",
        "source_type": "dedicated",
    },
    {
        "name": "FAPESP - Auxílio Inovação Regular",
        "url": "https://fapesp.br/18067",
        "source": "fapesp",
        "source_type": "dedicated",
    },
    {
        "name": "FAPESC - Chamada 37/2026",
        "url": "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-37-2026-programa-de-ciencia-tecnologia-e-inovacao-para-apoio-aos-grupos-de-pesquisa-da-udesc",
        "source": "fapesc",
        "source_type": "dedicated",
    },
    {
        "name": "Programa Centelha",
        "url": "https://programacentelha.com.br",
        "source": "centelha",
        "source_type": "programa",
    },
    {
        "name": "CONFAP - Horizon Europe (pending discovery)",
        "url": "https://confap.org.br/pt/editais/49/horizon-europe",
        "source": "confap",
        "source_type": "discovery",
    },
]

# Schema de extração (igual ao da Discovery, mas com secoes + pdfs)
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "prazo_envio": {"type": "string"},
        "publico_alvo": {"type": "string"},
        "descricao": {"type": "string"},
        "status": {"type": "string", "enum": ["ABERTA", "ENCERRADA", ""]},
        "opportunity_type": {"type": "string", "enum": ["edital", "desafio", "programa"]},
        "tema": {"type": "array", "items": {"type": "string"}},
        "tema_livre": {"type": "array", "items": {"type": "string"}},
        "secoes": {
            "type": "object",
            "properties": {
                "resumo": {"type": "string"},
                "descricao_completa": {"type": "string"},
                "quem_pode_participar": {"type": "string"},
                "cronograma": {"type": "string"},
                "requisitos": {"type": "string"},
                "categorias_financiamento": {"type": "string"},
                "faq": {"type": "string"},
            },
        },
        "pdf_urls": {"type": "array", "items": {"type": "string"}},
        "pdf_legendas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titulo", "descricao"],
}

EXTRACTION_INSTRUCTION = """
Extraia os dados completos desta oportunidade de financiamento/fomento/inovação.
Preencha TODOS os campos que conseguir encontrar.
Para `secoes`, extraia o texto completo de cada seção identificada.
Para `pdf_urls`, liste TODAS as URLs de PDF encontradas.
Para `pdf_legendas`, liste o anchor text de cada PDF na mesma ordem.
Responda APENAS JSON válido, sem markdown, sem comentários.
"""


# =============================================================================
# Helpers (standalone, sem import do core)
# =============================================================================

_YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _parse_wiki_yaml(source: str) -> dict:
    path = WIKIS_DIR / f"{source}.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    merged = {}
    for match in _YAML_BLOCK_RE.finditer(text):
        block = yaml.safe_load(match.group(1))
        if isinstance(block, dict):
            merged.update(block)
    return merged


def _skip_keywords(source: str) -> list[str]:
    cfg = _parse_wiki_yaml(source)
    return cfg.get("skip_keywords", [])


def _normalize_extraction(extracted: object) -> dict:
    if isinstance(extracted, dict):
        return extracted
    if isinstance(extracted, list):
        if not extracted:
            return {}
        first = extracted[0]
        if isinstance(first, dict):
            if "content" in first and isinstance(first["content"], str):
                if not first.get("error"):
                    try:
                        inner = json.loads(first["content"])
                        if isinstance(inner, dict):
                            return inner
                        if isinstance(inner, list):
                            return _normalize_extraction(inner)
                    except (json.JSONDecodeError, TypeError):
                        pass
            return first
        if isinstance(first, str):
            try:
                inner = json.loads(first)
                return _normalize_extraction(inner)
            except (json.JSONDecodeError, TypeError):
                pass
        return {}
    if isinstance(extracted, str):
        try:
            inner = json.loads(extracted)
            return _normalize_extraction(inner)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _should_skip_pdf(pdf_url: str, pdf_legenda: str, skip: list[str]) -> bool:
    stem = os.path.splitext(os.path.basename(pdf_url.rstrip("/").split("?")[0]))[0].lower()
    legenda = (pdf_legenda or "").lower()
    for kw in skip:
        if kw in stem or kw in legenda:
            return True
    return False


def _download_and_extract_pdf(url: str, timeout: int = 30) -> str | None:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        texts = []
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texts.append(t)
        return "\n\n".join(texts) if texts else None
    except Exception:
        return None


def _web_url_hash(url: str) -> str:
    import hashlib
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:16]


# =============================================================================
# Simulação do método ATUAL (Discovery _extract)
# =============================================================================

def _simulate_current_extraction(page_text: str, title: str, url: str, agency: str) -> dict:
    """LLM em 6k chars, igual ao _extract atual. Sem PDFs, sem seções."""
    from core.kg import schema as ws
    from core.llm.llm_client import make_client

    api_key = os.environ.get("OPENAI_API_KEY")
    client = make_client(api_key=api_key)
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    vocab = ws.tema_vocab()
    system = (
        "Extraia os campos de uma oportunidade de fomento a partir do texto. "
        "Responda só JSON com as chaves: titulo, prazo_envio (dd/mm/yyyy ou \"\"), "
        "publico_alvo, descricao (2-3 frases), status (ABERTA|ENCERRADA|\"\"), "
        "opportunity_type (UM de: edital|desafio|programa), "
        "tema (lista; ESCOLHA só desta lista canônica, [] se nenhum servir: "
        f"{vocab}), "
        "tema_livre (lista; 1-2 temas em 2-4 palavras quando NENHUM item de tema servir; "
        "[] caso contrário). Não invente dados que não estão no texto."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": f"Título: {title}\nURL: {url}\n\nTEXTO:\n{page_text[:6000]}"}],
            temperature=0,
            max_tokens=1200,
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        data = json.loads(raw)
    except Exception as e:
        return {"error": str(e), "simulated": True}

    tema = data.get("tema") or []
    if isinstance(tema, str):
        tema = [tema]
    tema_livre = data.get("tema_livre") or []
    if isinstance(tema_livre, str):
        tema_livre = [tema_livre]

    return {
        "url": url,
        "title": data.get("titulo") or title,
        "texto_cru": page_text[:6000],
        "prazo_envio": data.get("prazo_envio", ""),
        "publico_alvo": data.get("publico_alvo", ""),
        "descricao": data.get("descricao", ""),
        "status": data.get("status", "") or "ABERTA",
        "tema": "; ".join(t for t in tema if isinstance(t, str)),
        "tema_livre": "; ".join(t for t in tema_livre if isinstance(t, str)),
        "opportunity_type": (data.get("opportunity_type") or "edital").strip().lower(),
        "agency": agency or "",
        "fonte": agency or "Web (descoberta)",
    }


# =============================================================================
# Pipeline novo: Crawl4AI + BM25 + PDF + merge
# =============================================================================

async def pipeline_new(name: str, url: str, source: str, crawler: AsyncWebCrawler, page_text_bruto: str) -> dict:
    """Pipeline Discovery v2 com Crawl4AI.

    Retorna dict no formato discovered_opportunities enriquecido.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    skip = _skip_keywords(source) if source else []

    llm_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(provider="openai/gpt-4o-mini", api_token=api_key),
        schema=EXTRACTION_SCHEMA,
        extraction_type="schema",
        instruction=EXTRACTION_INSTRUCTION,
        input_format="markdown",
        temperature=0.0,
    )

    bm25 = BM25ContentFilter(
        user_query="edital chamada pública fomento subvenção inovação pesquisa financiamento",
        bm25_threshold=0.8,
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        extraction_strategy=llm_strategy,
        markdown_generator=DefaultMarkdownGenerator(content_filter=bm25),
        verbose=False,
    )

    start = time.perf_counter()
    result = await crawler.arun(url=url, config=run_config)
    elapsed = time.perf_counter() - start

    md_obj = result.markdown or ""
    raw_md = md_obj.raw_markdown if hasattr(md_obj, "raw_markdown") else str(md_obj)
    fit_md = md_obj.fit_markdown if hasattr(md_obj, "fit_markdown") else ""

    llm_extraction = {}
    if result.extracted_content:
        try:
            parsed = json.loads(result.extracted_content)
            llm_extraction = _normalize_extraction(parsed)
        except (json.JSONDecodeError, TypeError):
            llm_extraction = {"raw": str(result.extracted_content)[:2000]}
    else:
        llm_extraction = {"raw": "no extracted_content returned"}

    # PDFs
    pdf_urls = llm_extraction.get("pdf_urls", []) or []
    pdf_legendas = llm_extraction.get("pdf_legendas", []) or []
    pdf_texts: list[str] = []
    for i, pdf_url in enumerate(pdf_urls):
        legenda = pdf_legendas[i] if i < len(pdf_legendas) else ""
        if _should_skip_pdf(pdf_url, legenda, skip):
            continue
        text = _download_and_extract_pdf(pdf_url)
        if text:
            pdf_texts.append(text)

    # Merged text: fit_markdown (ou raw) + PDF texts
    texto_extraido = fit_md or raw_md or ""
    if pdf_texts:
        texto_extraido += "\n\n--- PDFs ---\n" + "\n\n".join(
            t for t in pdf_texts if t and not t.startswith("[erro")
        )

    tema = llm_extraction.get("tema") or []
    tema_livre = llm_extraction.get("tema_livre") or []

    return {
        "url": url,
        "url_hash": _web_url_hash(url),
        "title": llm_extraction.get("titulo") or name,
        "texto_cru": texto_extraido,
        "fit_markdown_len": len(fit_md or ""),
        "raw_markdown_len": len(raw_md or ""),
        "pdf_texts_total_chars": sum(len(t) for t in pdf_texts),
        "prazo_envio": llm_extraction.get("prazo_envio", ""),
        "publico_alvo": llm_extraction.get("publico_alvo", ""),
        "descricao": llm_extraction.get("descricao", ""),
        "status": llm_extraction.get("status", "") or "ABERTA",
        "tema": "; ".join(t for t in tema if isinstance(t, str)),
        "tema_livre": "; ".join(t for t in tema_livre if isinstance(t, str)),
        "opportunity_type": (llm_extraction.get("opportunity_type") or "edital").strip().lower(),
        "secoes": llm_extraction.get("secoes", {}),
        "source": source,
        "pdf_total": len(pdf_urls),
        "pdf_texts_loaded": len(pdf_texts),
        "crawl_duration_ms": round(elapsed * 1000),
    }


# =============================================================================
# Main
# =============================================================================

async def main():
    print("Pipeline Discovery — Atual × Crawl4AI v2")
    print(f"Data: {datetime.now(timezone.utc).isoformat()}")
    print()

    async with AsyncWebCrawler(verbose=False) as crawler:
        print("Warmup...")
        await crawler.arun(url="https://httpbin.org/html", config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))

        rows = []
        for t in TEST_URLS:
            name, url, source = t["name"], t["url"], t["source"]

            print(f"\n{'='*60}")
            print(f"Processando: {name}")
            print(f"{'='*60}")

            # 1. Fetch bruto (simula o _page_text atual — trafilatura)
            from core.llm.agent_tools.profile_tools import _fetch_and_parse
            page_data = _fetch_and_parse(url) or {}
            page_text_bruto = page_data.get("text", "")

            # 2. Método atual
            print("  [Atual] LLM em 6k chars...")
            current_result = _simulate_current_extraction(page_text_bruto, name, url, source)

            # 3. Pipeline novo
            print("  [Novo] Crawl4AI + BM25 + schema + PDF...")
            new_result = await pipeline_new(name, url, source, crawler, page_text_bruto)

            rows.append({
                "name": name,
                "url": url,
                "source": source,
                "current": {
                    **current_result,
                    "fetch_chars": len(page_text_bruto),
                    "llm_input_chars": min(len(page_text_bruto), 6000),
                },
                "new": new_result,
            })

            # Print summary
            c = rows[-1]["current"]
            n = rows[-1]["new"]
            print("\n  ┌── Comparação ──────────────────────────────")
            print("  │ Métrica               | Atual          | Novo")
            print("  │───────────────────────|────────────────|──────────────")
            print(f"  │ chars input LLM       | {c['llm_input_chars']:>6,}        | {n.get('fit_markdown_len', 0):>6,}")
            print(f"  │ chars texto_cru       | {len(str(c.get('texto_cru',''))):>6,}        | {len(str(n.get('texto_cru',''))):>6,}")
            print(f"  │ seções preenchidas    | N/A (plano)   | {sum(1 for v in (n.get('secoes',{}) or {}).values() if v and len(str(v))>50)}/7")
            print(f"  │ PDFs processados      | 0             | {n.get('pdf_texts_loaded', 0)}")
            print(f"  │ título                | {str(c.get('title',''))[:40]:40s} | {str(n.get('title',''))[:40]:40s}")
            print(f"  │ prazo                 | {str(c.get('prazo_envio','')):20s} | {str(n.get('prazo_envio','')):20s}")

    # Salva
    output = {
        "metadata": {
            "test_date": datetime.now(timezone.utc).isoformat(),
            "total_urls": len(TEST_URLS),
        },
        "results": rows,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Resumo
    print(f"\n{'='*60}")
    print("RESUMO — Discovery Pipeline Comparison")
    print(f"{'='*60}")
    print()
    print(f"  Resultados salvos em: {RESULTS_PATH}")

    # Tabela
    print(f"\n  {'URL':30s} | {'Atual chars':>12s} | {'Novo chars':>12s} | {'BM25%':>6s} | {'PDFs':>4s} | {'Seções':>6s}")
    print(f"  {'-'*30} | {'-'*12} | {'-'*12} | {'-'*6} | {'-'*4} | {'-'*6}")
    for r in rows:
        c = r["current"]
        n = r["new"]
        fit_len = n.get("fit_markdown_len", 0)
        raw_len = n.get("raw_markdown_len", 1)
        bm25_pct = round((1 - fit_len / max(raw_len, 1)) * 100, 0)
        filled = sum(1 for v in (n.get("secoes", {}) or {}).values() if v and len(str(v)) > 50)
        print(f"  {r['name'][:28]:28s} | {c['llm_input_chars']:>10,} | {fit_len:>10,} | {bm25_pct:>4.0f}% | {n.get('pdf_texts_loaded',0):>3d} | {filled}/7")


if __name__ == "__main__":
    asyncio.run(main())
