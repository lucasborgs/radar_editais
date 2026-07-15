"""Teste controlado v2: Crawl4AI com BM25 filter + PDF download + skip-lists.

Compara resultado com v1 (eval_crawl4ai.py) para medir ganho real.

Uso:
  python scripts/eval_crawl4ai_v2.py
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
RESULTS_PATH = os.path.join(SCRIPT_DIR, "eval_crawl4ai_v2_results.json")
WIKIS_DIR = Path(__file__).resolve().parent.parent / "wikis"

TEST_URLS = [
    {
        "name": "FAPEMIG - Sede Compete Minas",
        "url": "https://fapemig.br/oportunidades/chamadas-e-editais/fapemig-sede-compete-minas",
        "source": "fapemig",
    },
    {
        "name": "FINEP - Mais Inovação (Mobilidade Sustentável)",
        "url": "https://www.finep.gov.br/e/chamada-publica/222684/755376",
        "source": "finep",
    },
    {
        "name": "FAPESP - Auxílio Inovação Regular",
        "url": "https://fapesp.br/18067",
        "source": "fapesp",
    },
    {
        "name": "FAPESC - Chamada 37/2026",
        "url": "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-37-2026-programa-de-ciencia-tecnologia-e-inovacao-para-apoio-aos-grupos-de-pesquisa-da-udesc",
        "source": "fapesc",
    },
    {
        "name": "Programa Centelha",
        "url": "https://programacentelha.com.br",
        "source": "centelha",
    },
    {
        "name": "CONFAP - Horizon Europe (pending discovery)",
        "url": "https://confap.org.br/pt/editais/49/horizon-europe",
        "source": "confap",
    },
]

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "description": "Título completo da oportunidade"},
        "prazo_envio": {"type": "string", "description": "Prazo de inscrição no formato dd/mm/yyyy ou string vazia se não encontrado"},
        "publico_alvo": {"type": "string", "description": "Quem pode participar (público-alvo da oportunidade)"},
        "descricao": {"type": "string", "description": "Descrição de 2-3 frases sobre a oportunidade"},
        "status": {"type": "string", "enum": ["ABERTA", "ENCERRADA", ""], "description": "Status atual da oportunidade"},
        "opportunity_type": {"type": "string", "enum": ["edital", "desafio", "programa"], "description": "Tipo da oportunidade"},
        "tema": {"type": "array", "items": {"type": "string"}, "description": "Lista de temas do vocabulário canônico"},
        "tema_livre": {"type": "array", "items": {"type": "string"}, "description": "Temas livres fora do vocabulário canônico (1-2)"},
        "secoes": {
            "type": "object",
            "properties": {
                "resumo": {"type": "string", "description": "Resumo ou sumário executivo da chamada"},
                "descricao_completa": {"type": "string", "description": "Descrição completa e detalhada da oportunidade"},
                "quem_pode_participar": {"type": "string", "description": "Critérios de elegibilidade e quem pode participar"},
                "cronograma": {"type": "string", "description": "Datas e prazos do cronograma"},
                "requisitos": {"type": "string", "description": "Principais requisitos para submissão"},
                "categorias_financiamento": {"type": "string", "description": "Categorias de financiamento, valores, faixas de aporte"},
                "faq": {"type": "string", "description": "Perguntas frequentes sobre a oportunidade"},
            },
        },
        "pdf_urls": {"type": "array", "items": {"type": "string"}, "description": "URLs de documentos PDF encontrados na página"},
        "pdf_legendas": {"type": "array", "items": {"type": "string"}, "description": "Texto-âncora de cada PDF na mesma ordem de pdf_urls"},
    },
    "required": ["titulo", "descricao"],
}

EXTRACTION_INSTRUCTION = """
Extraia os dados completos desta oportunidade de financiamento/fomento/inovação.
Preencha TODOS os campos que conseguir encontrar no texto da página.
Para `secoes`, extraia o texto completo de cada seção identificada — não resuma.
Para `pdf_urls`, liste TODAS as URLs de PDF encontradas na página.
Para `pdf_legendas`, liste o texto do link (anchor text) de cada PDF na mesma ordem.
Responda APENAS JSON válido, sem markdown, sem comentários.
"""

# =============================================================================
# Skip-lists: mesma lógica de core/kg/schema.py (lê YAML de wikis/*.md)
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
    """True se o PDF deve ser ignorado (match na skip-list por substring)."""
    stem = os.path.splitext(os.path.basename(pdf_url.rstrip("/").split("?")[0]))[0].lower()
    legenda = (pdf_legenda or "").lower()
    for kw in skip:
        if kw in stem or kw in legenda:
            return True
    return False


def _download_and_extract_pdf(url: str, timeout: int = 30) -> str | None:
    """Baixa PDF e extrai texto com pdfplumber. None em falha."""
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
    except Exception as e:
        return f"[erro ao baixar PDF: {e}]"


# =============================================================================
# Métricas de qualidade
# =============================================================================

def _count_sections(markdown: str) -> int:
    return len(re.findall(r"^#{1,3}\s", markdown, re.MULTILINE))


def _quality_assessment(extraction: dict, markdown_len: int, pdf_texts: list[str]) -> str:
    desc = extraction.get("descricao", "") or ""
    secoes = extraction.get("secoes", {}) or {}
    filled_sections = sum(1 for v in secoes.values() if v and len(str(v).strip()) > 50)
    pdfs = extraction.get("pdf_urls", []) or []
    has_pdf_text = any(t and len(t) > 200 for t in pdf_texts)
    if len(desc) >= 500 and filled_sections >= 3:
        return "high"
    if len(desc) >= 200 or filled_sections >= 1 or len(pdfs) > 0 or has_pdf_text:
        return "medium"
    return "low"


# =============================================================================
# Extração principal
# =============================================================================

async def extract_url(name: str, url: str, source: str, crawler: AsyncWebCrawler) -> dict:
    print(f"\n{'='*60}")
    print(f"Extraindo: {name}")
    print(f"  URL: {url}")
    print(f"  Fonte: {source}")
    print(f"{'='*60}")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable required")

    skip = _skip_keywords(source) if source else []
    print(f"  Skip-list: {skip if skip else '(nenhuma)'}")

    llm_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(provider="openai/gpt-4o-mini", api_token=api_key),
        schema=EXTRACTION_SCHEMA,
        extraction_type="schema",
        instruction=EXTRACTION_INSTRUCTION,
        input_format="markdown",
        temperature=0.0,
    )

    # BM25 filter: query relevante para oportunidades de fomento
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
    try:
        result = await crawler.arun(url=url, config=run_config)
        elapsed = time.perf_counter() - start
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "name": name,
            "url": url,
            "source": source,
            "success": False,
            "error": str(e),
            "crawl_duration_ms": round(elapsed * 1000),
        }

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

    # PDFs detectados pela LLM
    pdf_urls = llm_extraction.get("pdf_urls", []) or []
    pdf_legendas = llm_extraction.get("pdf_legendas", []) or []

    # Baixa texto dos PDFs (aplicando skip-list)
    pdf_texts: list[str] = []
    pdfs_baixados = 0
    pdfs_skipped = 0
    for i, pdf_url in enumerate(pdf_urls):
        legenda = pdf_legendas[i] if i < len(pdf_legendas) else ""
        if _should_skip_pdf(pdf_url, legenda, skip):
            pdfs_skipped += 1
            continue
        text = _download_and_extract_pdf(pdf_url)
        if text and not text.startswith("[erro"):
            pdf_texts.append(text)
            pdfs_baixados += 1
        print(f"  📎 PDF baixado ({pdfs_baixados}): {os.path.basename(pdf_url.split('?')[0])} — {len(text or ''):,} chars")

    # Qualidade
    sections_count = _count_sections(fit_md or raw_md or "")
    quality = _quality_assessment(llm_extraction, len(fit_md or raw_md or ""), pdf_texts)
    secoes = llm_extraction.get("secoes", {}) or {}
    filled_secoes = sum(1 for v in secoes.values() if v and len(str(v).strip()) > 50)

    print(f"  ✓ Tempo: {elapsed:.1f}s")
    print(f"  ✓ Markdown raw: {len(raw_md or ''):,} chars")
    print(f"  ✓ Markdown fit (BM25): {len(fit_md or ''):,} chars ({(len(fit_md or '')/max(len(raw_md or ''),1)*100):.0f}% do raw)")
    print(f"  ✓ Extração: qualidade={quality}, seções preenchidas={filled_secoes}/7")
    print(f"  ✓ PDFs detectados: {len(pdf_urls)}, baixados: {pdfs_baixados}, skipped: {pdfs_skipped}")
    if pdf_texts:
        total_pdf_chars = sum(len(t) for t in pdf_texts)
        print(f"  ✓ Texto extraído dos PDFs: {total_pdf_chars:,} chars")
    if llm_extraction.get("titulo"):
        print(f"  ✓ Título: {llm_extraction['titulo'][:80]}")
    if llm_extraction.get("prazo_envio"):
        print(f"  ✓ Prazo: {llm_extraction['prazo_envio']}")

    return {
        "name": name,
        "url": url,
        "source": source,
        "success": True,
        "crawl_duration_ms": round(elapsed * 1000),
        "markdown_raw_len": len(raw_md or ""),
        "markdown_fit_len": len(fit_md or ""),
        "markdown_sections": sections_count,
        "bm25_reduction_pct": round((1 - len(fit_md or "") / max(len(raw_md or ""), 1)) * 100, 1),
        "llm_extraction": llm_extraction,
        "pdf_total": len(pdf_urls),
        "pdf_baixados": pdfs_baixados,
        "pdf_skipped": pdfs_skipped,
        "pdf_texts_chars": [len(t) for t in pdf_texts],
        "filled_secoes": filled_secoes,
        "extraction_quality": quality,
    }


async def main():
    print("Crawl4AI Evaluation v2 — BM25 + PDF download + skip-lists")
    print(f"Data: {datetime.now(timezone.utc).isoformat()}")
    print()

    results = []

    async with AsyncWebCrawler(verbose=False) as crawler:
        print("Warmup...")
        await crawler.arun(url="https://httpbin.org/html", config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))

        for t in TEST_URLS:
            r = await extract_url(t["name"], t["url"], t["source"], crawler)
            results.append(r)

    # Salva
    output = {
        "metadata": {
            "test_date": datetime.now(timezone.utc).isoformat(),
            "crawl4ai_version": "0.9.1",
            "llm_model": "gpt-4o-mini",
            "bm25_threshold": 0.8,
            "total_urls": len(TEST_URLS),
        },
        "results": results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Resumo
    print(f"\n{'='*60}")
    print("RESUMO v2")
    print(f"{'='*60}")
    success = sum(1 for r in results if r.get("success"))
    high = sum(1 for r in results if r.get("extraction_quality") == "high")
    medium = sum(1 for r in results if r.get("extraction_quality") == "medium")
    low = sum(1 for r in results if r.get("extraction_quality") == "low")
    avg_time = sum(r.get("crawl_duration_ms", 0) for r in results) / len(results)
    total_pdfs_detectados = sum(r.get("pdf_total", 0) for r in results)
    total_pdfs_baixados = sum(r.get("pdf_baixados", 0) for r in results)
    total_pdfs_skipped = sum(r.get("pdf_skipped", 0) for r in results)
    avg_bm25_reduction = sum(r.get("bm25_reduction_pct", 0) for r in results) / len(results)

    print(f"  Sucesso: {success}/{len(TEST_URLS)}")
    print(f"  Qualidade: high={high}  medium={medium}  low={low}")
    print(f"  Tempo médio: {avg_time/1000:.1f}s")
    print(f"  Redução BM25 média: {avg_bm25_reduction:.0f}%")
    print(f"  PDFs: detectados={total_pdfs_detectados} baixados={total_pdfs_baixados} skipped={total_pdfs_skipped}")
    print(f"  Resultados salvos em: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
