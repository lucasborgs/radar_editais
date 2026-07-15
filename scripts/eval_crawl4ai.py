"""Teste controlado: Crawl4AI como extrator universal de oportunidades.

Uso:
  pip install -U crawl4ai && crawl4ai-setup   # já feito
  python scripts/eval_crawl4ai.py

Saída: scripts/eval_crawl4ai_results.json + resumo no stdout.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone

from crawl4ai import AsyncWebCrawler, LLMConfig, LLMExtractionStrategy, CrawlerRunConfig, CacheMode

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(SCRIPT_DIR, "eval_crawl4ai_results.json")

TEST_URLS = [
    {
        "name": "FAPEMIG - Sede Compete Minas",
        "url": "https://fapemig.br/oportunidades/chamadas-e-editais/fapemig-sede-compete-minas",
        "source_type": "discovery",
    },
    {
        "name": "FINEP - Mais Inovação (Mobilidade Sustentável)",
        "url": "https://www.finep.gov.br/e/chamada-publica/222684/755376",
        "source_type": "dedicated",
    },
    {
        "name": "FAPESP - Auxílio Inovação Regular",
        "url": "https://fapesp.br/18067",
        "source_type": "dedicated",
    },
    {
        "name": "FAPESC - Chamada 37/2026",
        "url": "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-37-2026-programa-de-ciencia-tecnologia-e-inovacao-para-apoio-aos-grupos-de-pesquisa-da-udesc",
        "source_type": "dedicated",
    },
    {
        "name": "Programa Centelha",
        "url": "https://programacentelha.com.br",
        "source_type": "programa",
    },
    {
        "name": "CONFAP - Horizon Europe (pending discovery)",
        "url": "https://confap.org.br/pt/editais/49/horizon-europe",
        "source_type": "discovery",
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
        "pdf_urls": {"type": "array", "items": {"type": "string"}, "description": "URLs de documentos PDF encontrados na página (regulamento, anexos, editais)"},
    },
    "required": ["titulo", "descricao"],
}

EXTRACTION_INSTRUCTION = """
Extraia os dados completos desta oportunidade de financiamento/fomento/inovação.

Preencha TODOS os campos que conseguir encontrar no texto da página.
Para `secoes`, extraia o texto completo de cada seção identificada — não resuma.
Para `pdf_urls`, liste TODAS as URLs de PDF encontradas na página.

Responda APENAS JSON válido, sem markdown, sem comentários.
"""


def _normalize_extraction(extracted: object) -> dict:
    """Retorna dict normalizado a partir de extração que pode ser list, dict ou str.

    Crawl4AI schema extraction retorna lista de {index, error, tags, content}
    onde content é uma string JSON.
    """
    if isinstance(extracted, dict):
        return extracted
    if isinstance(extracted, list):
        if not extracted:
            return {}
        first = extracted[0]
        if isinstance(first, dict):
            # Crawl4AI nested format: {index, error, tags, content}
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


def _count_sections(markdown: str) -> int:
    return len(re.findall(r"^#{1,3}\s", markdown, re.MULTILINE))


def _quality_assessment(extraction: dict, markdown_len: int) -> str:
    desc = extraction.get("descricao", "") or ""
    secoes = extraction.get("secoes", {}) or {}
    filled_sections = sum(1 for v in secoes.values() if v and len(str(v).strip()) > 50)
    pdfs = extraction.get("pdf_urls", []) or []
    if len(desc) >= 500 and filled_sections >= 3:
        return "high"
    if len(desc) >= 200 or filled_sections >= 1 or len(pdfs) > 0:
        return "medium"
    return "low"


async def extract_url(name: str, url: str, source_type: str, crawler: AsyncWebCrawler) -> dict:
    print(f"\n{'='*60}")
    print(f"Extraindo: {name}")
    print(f"  URL: {url}")
    print(f"{'='*60}")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable required")

    llm_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(provider="openai/gpt-4o-mini", api_token=api_key),
        schema=EXTRACTION_SCHEMA,
        extraction_type="schema",
        instruction=EXTRACTION_INSTRUCTION,
        input_format="markdown",
        temperature=0.0,
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        extraction_strategy=llm_strategy,
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
            "source_type": source_type,
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

    quality = _quality_assessment(llm_extraction, len(raw_md or ""))
    sections_count = _count_sections(raw_md or "")
    pdfs = llm_extraction.get("pdf_urls", []) or []
    secoes = llm_extraction.get("secoes", {}) or {}
    filled_secoes = sum(1 for v in secoes.values() if v and len(str(v).strip()) > 50)

    print(f"  ✓ Tempo: {elapsed:.1f}s")
    print(f"  ✓ Markdown: {len(raw_md or ''):,} chars, ~{sections_count} seções")
    print(f"  ✓ Extração: qualidade={quality}, seções preenchidas={filled_secoes}/7, PDFs={len(pdfs)}")
    if llm_extraction.get("titulo"):
        print(f"  ✓ Título: {llm_extraction['titulo'][:80]}")
    if llm_extraction.get("prazo_envio"):
        print(f"  ✓ Prazo: {llm_extraction['prazo_envio']}")
    if pdfs:
        for p in pdfs[:3]:
            print(f"  📎 PDF: {p[:100]}")
        if len(pdfs) > 3:
            print(f"     ... e mais {len(pdfs) - 3} PDFs")

    return {
        "name": name,
        "url": url,
        "source_type": source_type,
        "success": True,
        "crawl_duration_ms": round(elapsed * 1000),
        "markdown_len": len(raw_md or ""),
        "markdown_sections": sections_count,
        "fit_markdown_len": len(fit_md or ""),
        "llm_extraction": llm_extraction,
        "has_pdfs": len(pdfs) > 0,
        "pdf_count": len(pdfs),
        "filled_secoes": filled_secoes,
        "extraction_quality": quality,
    }


async def main():
    print("Crawl4AI Evaluation — Extractor Universal de Oportunidades")
    print(f"Data: {datetime.now(timezone.utc).isoformat()}")
    print(f"URLs: {len(TEST_URLS)}")
    print()

    results = []
    # warmup: força download do browser + cache
    async with AsyncWebCrawler(verbose=False) as crawler:
        print("Warmup...")
        await crawler.arun(url="https://httpbin.org/html", config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))

        for t in TEST_URLS:
            r = await extract_url(t["name"], t["url"], t["source_type"], crawler)
            results.append(r)

    # salva resultados
    output = {
        "metadata": {
            "test_date": datetime.now(timezone.utc).isoformat(),
            "crawl4ai_version": "0.9.1",
            "llm_model": "gpt-4o-mini",
            "total_urls": len(TEST_URLS),
        },
        "results": results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # resumo final
    print(f"\n{'='*60}")
    print("RESUMO")
    print(f"{'='*60}")
    successes = sum(1 for r in results if r.get("success"))
    high = sum(1 for r in results if r.get("extraction_quality") == "high")
    medium = sum(1 for r in results if r.get("extraction_quality") == "medium")
    low = sum(1 for r in results if r.get("extraction_quality") == "low")
    avg_time = sum(r.get("crawl_duration_ms", 0) for r in results) / len(results)
    total_pdfs = sum(r.get("pdf_count", 0) for r in results)

    print(f"  Sucesso: {successes}/{len(TEST_URLS)}")
    print(f"  Qualidade: high={high}  medium={medium}  low={low}")
    print(f"  Tempo médio: {avg_time/1000:.1f}s")
    print(f"  Total PDFs detectados: {total_pdfs}")
    print(f"  Resultados salvos em: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
