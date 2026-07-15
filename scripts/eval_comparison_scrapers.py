"""Comparação Crawl4AI × scrapers dedicados para FINEP, FAPESP, FAPESC.

Lê o bronze mais recente de cada scraper e compara com o output do Crawl4AI
v2 (eval_crawl4ai_v2_results.json), campo a campo.

Uso:
  python scripts/eval_comparison_scrapers.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
V2_RESULTS = os.path.join(SCRIPT_DIR, "eval_crawl4ai_v2_results.json")
RESULTS_PATH = os.path.join(SCRIPT_DIR, "eval_comparison_scrapers_results.json")

BRONZE_DIR = Path(__file__).resolve().parent.parent / "data" / "bronze"

SOURCES = {
    "finep": {
        "name": "FINEP - Mais Inovação (Mobilidade Sustentável)",
        "bronze_glob": "finep_raw/finep_chamadas_*.json",
        "match_fn": lambda c: c.get("link", "") == "https://www.finep.gov.br/e/chamada-publica/222684/755376",
    },
    "fapesp": {
        "name": "FAPESP - Auxílio Inovação Regular",
        "bronze_glob": "fapesp_raw/fapesp_scan_*.json",
        "match_fn": lambda c: str(c.get("url", "")) == "https://fapesp.br/18067",
    },
    "fapesc": {
        "name": "FAPESC - Chamada 37/2026",
        "bronze_glob": "fapesc_raw/fapesc_scan_*.json",
        "match_fn": lambda c: str(c.get("native_id", "")) == "37-2026",
    },
}

# Mapeamento de campos: Crawl4AI → scraper
FIELD_MAP = {
    "titulo": {"title": "titulo", "finep": "titulo", "fapesp": "titulo", "fapesc": "titulo"},
    "prazo_envio": {"title": "prazo_envio", "finep": "prazo_envio", "fapesp": "data_limite", "fapesc": "data_limite"},
    "publico_alvo": {"title": "publico_alvo", "finep": "publico_alvo", "fapesp": None, "fapesc": None},
    "descricao": {"title": "descricao", "finep": "descricao", "fapesp": None, "fapesc": None},
    "status": {"title": "status", "finep": "status", "fapesp": "status", "fapesc": "status"},
    "tema": {"title": "tema", "finep": "tema", "fapesp": None, "fapesc": None},
    "texto_cru_chars": {"title": "texto_cru (chars)", "finep": "descricao (chars)", "fapesp": "texto_cru", "fapesc": "texto_cru"},
}


def _load_latest_bronze(bronze_glob: str) -> list[dict]:
    """Carrega o JSON bronze mais recente que casa o glob."""
    candidates = sorted(BRONZE_DIR.glob(bronze_glob))
    if not candidates:
        return []
    with open(candidates[-1]) as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _get_scraper_value(data: dict, field: str) -> str:
    """Extrai valor de campo do scraper, normalizando."""
    v = data.get(field)
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    return str(v)


def _get_crawl_value(crawl: dict, field: str) -> str:
    """Extrai valor de campo do Crawl4AI extração."""
    ext = crawl.get("llm_extraction", {}) or {}
    v = ext.get(field)
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)[:200]
    return str(v)


def main():
    # Carrega resultados do Crawl4AI v2
    if not os.path.exists(V2_RESULTS):
        print(f"ERRO: execute eval_crawl4ai_v2.py primeiro (faltam resultados em {V2_RESULTS})")
        return

    with open(V2_RESULTS) as f:
        v2_data = json.load(f)

    crawl_results = {r["source"]: r for r in v2_data["results"]}

    comparison = []

    for key, info in SOURCES.items():
        print(f"\n{'='*60}")
        print(f"Comparação: {info['name']}")
        print(f"{'='*60}")

        # Scraper data
        bronze = _load_latest_bronze(info["bronze_glob"])
        chamada = None
        for c in bronze:
            if info["match_fn"](c):
                chamada = c
                break

        if chamada is None:
            print(f"  ⚠ Chamada não encontrada no bronze para {key}")
            continue

        # Crawl4AI data
        crawl = crawl_results.get(key)
        if crawl is None:
            print(f"  ⚠ Crawl4AI result not found for {key}")
            continue

        ext = crawl.get("llm_extraction", {}) or {}
        secoes = ext.get("secoes", {}) or {}
        filled_secoes = sum(1 for v in secoes.values() if v and len(str(v).strip()) > 50)

        print(f"\n  {'Campo':30s} | {'Scraper':50s} | {'Crawl4AI':50s} | {'Match?'}")
        print(f"  {'-'*30} | {'-'*50} | {'-'*50} | {'-'*7}")

        fields_to_compare = [
            ("titulo", "titulo", "titulo"),
            ("prazo_envio", "prazo_envio", "prazo_envio"),
            ("publico_alvo", "publico_alvo", "publico_alvo"),
            ("status", "status", "status"),
            ("tema (aggregated)", "tema", "tema"),
            ("descricao", "descricao", "descricao"),
        ]

        crawl_text = crawl
        bronze_text = chamada

        for label, crawl_field, scraper_field in fields_to_compare:
            # Scraper field mapping: key-specific or generic
            if key == "finep":
                sf = scraper_field
            elif key == "fapesp":
                sf = {"titulo": "titulo", "prazo_envio": "data_limite", "publico_alvo": "modalidades",
                      "status": "status", "tema": "areas", "descricao": "texto_cru"}.get(scraper_field, scraper_field)
            elif key == "fapesc":
                sf = {"titulo": "titulo", "prazo_envio": "data_limite", "publico_alvo": "modalidades",
                      "status": "status", "tema": "areas", "descricao": "texto_cru"}.get(scraper_field, scraper_field)
            else:
                sf = scraper_field

            sv = _get_scraper_value(bronze_text, sf) if sf else ""
            cv = _get_crawl_value(crawl_text, crawl_field)
            match = "✓" if (sv.strip().lower() == cv.strip().lower()[:len(sv.strip())]
                           or not sv and not cv) else "~" if (sv and cv and (sv[:50] in cv or cv[:50] in sv)) else "✗"

            print(f"  {label:30s} | {sv[:48]:50s} | {cv[:48]:50s} | {match}")

        # Campos específicos por fonte
        print("\n  ── Campos específicos ──")

        if key == "finep":
            pdfs_scraper = chamada.get("pdf_urls", [])
            pdfs_crawl = ext.get("pdf_urls", [])
            print(f"  {'PDFs':30s} | {len(pdfs_scraper):>3d} URL(s)       | {len(pdfs_crawl):>3d} URL(s)")
            if pdfs_scraper and pdfs_crawl:
                shared = set(p.split("?")[0].rstrip("/") for p in pdfs_scraper) & set(p.split("?")[0].rstrip("/") for p in pdfs_crawl)
                print(f"  {'PDFs em comum':30s} | {len(shared):>3d}                            |")
            print(f"  {'taxonomy_categories':30s} | {str(chamada.get('api_taxonomy_categories', []))[:48]:50s} | {key:50s} | ✗")

        if key == "fapesp":
            print(f"  {'texto_cru (chars)':30s} | {len(chamada.get('texto_cru','')):>6,}                     | {crawl.get('markdown_raw_len',0):>6,}")
            print(f"  {'data_limite (formatted)':30s} | {chamada.get('data_limite',''):50s} | {ext.get('prazo_envio',''):50s}")

        if key == "fapesc":
            print(f"  {'PDF edital':30s} | {'Sim (baixado)':50s} | {str(ext.get('pdf_urls',[])[:1]):50s}")
            print(f"  {'content_source':30s} | {chamada.get('content_source',''):50s} | {'crawl4ai (html)':50s}")

        # Qualidade do texto
        scraper_text_chars = len(_get_scraper_value(bronze_text, "texto_cru" if "texto_cru" in bronze_text else "descricao"))
        crawl_text_chars = crawl.get("markdown_fit_len") or crawl.get("markdown_raw_len", 0) or 0
        print("\n  ── Qualidade do conteúdo ──")
        print(f"  {'texto útil (chars)':30s} | {scraper_text_chars:>6,}                     | {crawl_text_chars:>6,}")
        print(f"  {'seções preenchidas':30s} | {'N/A (texto plano)':50s} | {filled_secoes}/7")
        print(f"  {'PDFs baixados':30s} | {chamada.get('pdf_texts',{}) if isinstance(chamada.get('pdf_texts'), dict) else 'N/A'}")

        # Token estimation
        input_tokens_llm_crawl = (crawl.get("markdown_fit_len") or crawl.get("markdown_raw_len", 0) or 0) // 4
        print("\n  ── Custo estimado (por URL) ──")
        print(f"  {'tokens input LLM':30s} | {'0 (sem LLM)':50s} | ~{input_tokens_llm_crawl:,}")

        comparison.append({
            "source": key,
            "scraper": {
                "titulo": chamada.get("titulo", ""),
                "prazo_envio": chamada.get("prazo_envio") or chamada.get("data_limite", ""),
                "status": chamada.get("status", ""),
                "texto_chars": scraper_text_chars,
                "pdf_count": len(chamada.get("pdf_urls", [])) if key == "finep" else (1 if chamada.get("edital_pdf_url") else 0),
            },
            "crawl4ai": {
                "titulo": ext.get("titulo", ""),
                "prazo_envio": ext.get("prazo_envio", ""),
                "status": ext.get("status", ""),
                "texto_chars": crawl_text_chars,
                "pdf_count": len(ext.get("pdf_urls", [])),
                "filled_secoes": filled_secoes,
            },
        })

    # Salva
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "test_date": v2_data["metadata"]["test_date"],
                "bronze_dirs": {k: str(BRONZE_DIR / info["bronze_glob"]) for k, info in SOURCES.items()},
            },
            "comparison": comparison,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("Comparação salva em:", RESULTS_PATH)


if __name__ == "__main__":
    main()
