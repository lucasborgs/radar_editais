#!/usr/bin/env python3
"""
Scraper histórico de chamadas de propostas da FAPESP (2006-2026).

Para cada ano, acessa a página de listagem, coleta todos os links de chamadas
e salva o texto completo do corpo (div.page-body) de cada chamada em JSON.

Saída:
    bronze_data/fapesp_pages/{chamada_id}.json  — texto de cada chamada
    bronze_data/fapesp_page_manifest.json        — índice completo

Uso:
    python3 run_fapesp_pages.py                         # todos os anos
    python3 run_fapesp_pages.py --years 2023 2022 2021  # anos específicos
    python3 run_fapesp_pages.py --start-year 2018       # 2018 em diante
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# =============================================================================
# MAPEAMENTO ANO → URL (IDs obtidos do nav da própria FAPESP)
# =============================================================================

YEAR_URLS: dict[int, str] = {
    2026: "https://fapesp.br/2185/chamadas-de-propostas-2026",
    2025: "https://fapesp.br/17955/chamadas-de-propostas-2025",
    2024: "https://fapesp.br/17333/chamadas-de-propostas-2024",
    2023: "https://fapesp.br/16576/chamadas-de-propostas-2023",
    2022: "https://fapesp.br/15851/chamadas-de-propostas-2022",
    2021: "https://fapesp.br/15281/chamadas-de-propostas-2021",
    2020: "https://fapesp.br/14746/chamadas-de-propostas-2020",
    2019: "https://fapesp.br/13950/chamadas-de-propostas-2019",
    2018: "https://fapesp.br/12544/chamadas-de-propostas-2018",
    2017: "https://fapesp.br/11469/chamadas-de-propostas-2017",
    2016: "https://fapesp.br/10695/chamadas-de-propostas-2016",
    2015: "https://fapesp.br/9992/chamadas-de-propostas-2015",
    2014: "https://fapesp.br/9249/chamadas-de-propostas-2014",
    2013: "https://fapesp.br/8430/chamadas-de-propostas-2013",
    2012: "https://fapesp.br/7525/chamadas-de-propostas-2012",
    2011: "https://fapesp.br/6804/chamadas-de-propostas-2011",
    2010: "https://fapesp.br/6080/chamadas-de-propostas-2010",
    2009: "https://fapesp.br/5618/chamadas-de-propostas-2009",
    2008: "https://fapesp.br/5228/chamadas-de-propostas-2008",
    2007: "https://fapesp.br/4351/chamadas-de-propostas-2007",
    2006: "https://fapesp.br/4352/chamadas-de-propostas-2006",
}

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

OUTPUT_DIR = Path("bronze_data/fapesp_pages")
MANIFEST_FILE = Path("bronze_data/fapesp_page_manifest.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

SLEEP_BETWEEN_CHAMADAS = 0.8   # segundos entre detail pages
SLEEP_BETWEEN_YEARS = 2.0      # segundos entre anos

# Padrão de URL de chamada individual: https://fapesp.br/{numeric_id}
_CHAMADA_HREF_RE = re.compile(r"^https://fapesp\.br/(\d+)(?:/.*)?$")

# Títulos de sub-links que NÃO são chamadas (resultado, selecionados, etc.)
_SUBLINK_TITLE_RE = re.compile(
    r"^(resultado|selecionad|proposta[s]? selecionada|lista de|english version|"
    r"enquadramento|homologaç|errata|prorrogaç|recurso[s]?|corrigend|addendum|"
    r"retificaç|comunicado|aviso|nota )",
    re.I,
)

# Tags/seções a remover do page-body antes de extrair texto
_NOISE_SELECTORS = [
    "script", "style",
    ".a2a_kit",            # botões de compartilhamento AddToAny
    ".infoPost",           # rodapé com URL e data de publicação
    "hr",
]


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def fetch_html(url: str, timeout: int = 20) -> BeautifulSoup | None:
    """GET; segue meta-refresh HTML (FAPESP redireciona /id → /id/slug via meta-refresh).
    Retorna BeautifulSoup ou None em caso de falha."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Segue meta-refresh se presente (requests não segue redirecionamentos HTML)
        meta = soup.find("meta", attrs={"http-equiv": re.compile(r"^refresh$", re.I)})
        if meta:
            content = meta.get("content", "")
            m = re.search(r"URL=['\"]?([^'\">\s]+)", content, re.I)
            if m:
                redirect_url = m.group(1)
                resp2 = requests.get(redirect_url, headers=HEADERS, timeout=timeout)
                resp2.raise_for_status()
                return BeautifulSoup(resp2.text, "html.parser")

        return soup
    except Exception as e:
        print(f"    ERRO ao acessar {url}: {e}")
        return None


def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_manifest(manifest: dict) -> None:
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# =============================================================================
# LISTAGEM DE CHAMADAS POR ANO
# =============================================================================

def list_chamadas_do_ano(year: int, url: str) -> list[dict]:
    """
    Extrai todos os links de chamadas individuais da página do ano.

    Retorna lista de {chamada_id, titulo, url, ano}.
    """
    print(f"\n[{year}] Acessando: {url}")
    soup = fetch_html(url)
    if not soup:
        return []

    chamadas = []
    seen_ids: set[str] = set()

    # Escopa no div.page-body para evitar links de nav/rodapé.
    # Anos recentes (2020+): link plain <a> no topo do <li>.
    # Anos antigos (2015-): link dentro de <b><a>.
    # Em ambos os casos o link principal é SEMPRE o primeiro <a> de cada <li>.
    scope = soup.find("div", class_="page-body") or soup
    candidate_anchors = [
        li.find("a", href=_CHAMADA_HREF_RE)
        for li in scope.find_all("li")
    ]
    candidate_anchors = [a for a in candidate_anchors if a]

    for a in candidate_anchors:
        href = a["href"]
        m = _CHAMADA_HREF_RE.match(href)
        if not m:
            continue

        chamada_id = m.group(1)
        if chamada_id in seen_ids:
            continue

        # Ignora links para as próprias páginas de listagem por ano
        if any(str(nid) in href for nid in YEAR_URLS.values()
               if str(nid) in href):
            continue

        # Normaliza URL para forma canônica sem slug
        canonical_url = f"https://fapesp.br/{chamada_id}"

        titulo = a.get_text(strip=True)
        if not titulo or _SUBLINK_TITLE_RE.match(titulo):
            continue

        seen_ids.add(chamada_id)
        chamadas.append({
            "chamada_id": chamada_id,
            "titulo": titulo,
            "url": canonical_url,
            "ano": year,
        })

    print(f"[{year}] {len(chamadas)} chamada(s) encontrada(s).")
    return chamadas


# =============================================================================
# EXTRAÇÃO DE CONTEÚDO DA CHAMADA
# =============================================================================

def extract_page_body(soup: BeautifulSoup) -> str:
    """
    Extrai o texto limpo do div.page-body, removendo ruído (JS, botões sociais,
    rodapé de publicação).
    """
    body = soup.find("div", class_="page-body")
    if not body:
        return ""

    # Remove elementos de ruído
    for sel in _NOISE_SELECTORS:
        if sel.startswith("."):
            for el in body.find_all(class_=sel[1:]):
                el.decompose()
        else:
            for el in body.find_all(sel):
                el.decompose()

    text = body.get_text(separator="\n", strip=True)
    # Colapsa linhas em branco múltiplas
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def scrape_chamada(chamada: dict) -> dict | None:
    """
    Acessa a página da chamada e retorna o registro completo com body_text.
    Retorna None em caso de falha.
    """
    soup = fetch_html(chamada["url"])
    if not soup:
        return None

    body_text = extract_page_body(soup)
    if not body_text:
        print(f"    AVISO: body vazio para chamada {chamada['chamada_id']}")

    # Tenta pegar o título da página (mais limpo que o texto do link na listagem)
    h2 = soup.find("h2", class_="b")
    titulo_pagina = h2.get_text(strip=True) if h2 else chamada["titulo"]
    # Remove o sufixo "English version" se presente
    titulo_pagina = re.sub(r"\s*English version\s*$", "", titulo_pagina).strip()

    return {
        "chamada_id": chamada["chamada_id"],
        "titulo": titulo_pagina or chamada["titulo"],
        "url": chamada["url"],
        "ano": chamada["ano"],
        "body_text": body_text,
        "body_chars": len(body_text),
        "data_extracao": datetime.now().strftime("%Y-%m-%d"),
    }


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa texto de chamadas FAPESP (2006-2026)"
    )
    parser.add_argument(
        "--years", nargs="+", type=int,
        help="Anos específicos a processar (ex: --years 2023 2022)"
    )
    parser.add_argument(
        "--start-year", type=int, default=2006,
        help="Ano inicial (processa start-year até 2026, inclusive). Default: 2006"
    )
    args = parser.parse_args()

    if args.years:
        years_to_process = sorted(args.years, reverse=True)
    else:
        years_to_process = sorted(
            [y for y in YEAR_URLS if y >= args.start_year],
            reverse=True  # do mais recente para o mais antigo
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    print("=" * 70)
    print(f"FAPESP Page Scraper — {len(years_to_process)} ano(s): {years_to_process}")
    print(f"Saída    : {OUTPUT_DIR.resolve()}")
    print(f"Manifest : {MANIFEST_FILE.resolve()}")
    print("=" * 70)

    total_novos = 0
    total_skip = 0
    total_erro = 0

    for year in years_to_process:
        year_url = YEAR_URLS.get(year)
        if not year_url:
            print(f"[{year}] URL não mapeada — pulando.")
            continue

        chamadas = list_chamadas_do_ano(year, year_url)
        if not chamadas:
            time.sleep(SLEEP_BETWEEN_YEARS)
            continue

        for chamada in chamadas:
            cid = chamada["chamada_id"]
            dest = OUTPUT_DIR / f"{cid}.json"

            # Resume: pula se já existe COM conteúdo (body_chars > 0)
            if dest.exists():
                try:
                    existing = json.loads(dest.read_text(encoding="utf-8"))
                    if existing.get("body_chars", 0) > 0:
                        print(f"  [SKIP] {cid} — {chamada['titulo'][:60]}")
                        total_skip += 1
                        if cid not in manifest:
                            manifest[cid] = existing
                        continue
                    # body vazio → re-processa
                    print(f"  [RETRY] {cid} — body vazio, re-extraindo")
                except Exception:
                    pass

            time.sleep(SLEEP_BETWEEN_CHAMADAS)
            print(f"  [{cid}] {chamada['titulo'][:70]}")

            record = scrape_chamada(chamada)
            if not record:
                print(f"    FALHA ao obter chamada {cid}")
                total_erro += 1
                continue

            # Salva JSON individual
            dest.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # Atualiza manifest (sem body_text para não ficar enorme)
            manifest[cid] = {k: v for k, v in record.items() if k != "body_text"}
            save_manifest(manifest)

            total_novos += 1
            print(f"    OK — {record['body_chars']} chars")

        time.sleep(SLEEP_BETWEEN_YEARS)

    print("\n" + "=" * 70)
    print("RESUMO")
    print("=" * 70)
    print(f"  Novas baixadas   : {total_novos}")
    print(f"  Já existiam      : {total_skip}")
    print(f"  Erros            : {total_erro}")
    print(f"  Total no manifest: {len(manifest)}")
    print(f"  Manifest salvo em: {MANIFEST_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()
