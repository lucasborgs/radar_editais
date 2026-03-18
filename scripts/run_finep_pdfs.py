#!/usr/bin/env python3
"""
Baixa todos os PDFs das chamadas públicas FINEP (encerradas).

Estrutura de saída:
    bronze_data/finep_pdfs/
        {chamada_id}/
            regulamento.pdf
            anexo_1.pdf
            ...
    bronze_data/finep_pdf_manifest.json   ← metadados de cada chamada + PDFs baixados

Funcionalidades:
    - Retomada automática: pula PDFs já existentes em disco
    - Rate limiting: pausa entre chamadas e entre PDFs
    - Salva manifest incrementalmente (a cada chamada processada)

Uso:
    python3 run_finep_pdfs.py                      # todas as páginas de encerradas
    python3 run_finep_pdfs.py --max-pages 5        # limita a 5 páginas (~50 chamadas)
    python3 run_finep_pdfs.py --situacao todas     # sem filtro de situação
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

BASE_URL = "http://www.finep.gov.br"
LIST_URL = f"{BASE_URL}/chamadas-publicas/chamadaspublicas"
ITEMS_PER_PAGE = 10

OUTPUT_DIR = Path("bronze_data/finep_pdfs")
MANIFEST_FILE = Path("bronze_data/finep_pdf_manifest.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

SLEEP_BETWEEN_CHAMADAS = 1.0   # segundos
SLEEP_BETWEEN_PDFS = 0.3       # segundos
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB por PDF


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def fetch_html(url: str, timeout: int = 20) -> BeautifulSoup | None:
    """Faz GET e retorna BeautifulSoup. Retorna None em caso de falha."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"    ERRO ao acessar {url}: {e}")
        return None


def load_manifest() -> dict:
    """Carrega o manifest existente (mapa chamada_id → dict)."""
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_manifest(manifest: dict) -> None:
    """Salva manifest no disco."""
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =============================================================================
# PARSING DA LISTAGEM
# =============================================================================

def list_chamadas_page(situacao: str, start: int) -> tuple[list[dict], bool]:
    """
    Busca uma página da listagem de chamadas.

    Retorna: (lista de {chamada_id, titulo, link, status}, há_próxima_página)
    """
    params = f"?situacao={situacao}&filter_order=ordering&filter_order_Dir=asc&start={start}"
    url = LIST_URL + params
    print(f"  [Listagem] {url}")

    soup = fetch_html(url)
    if not soup:
        return [], False

    content = soup.find("div", id="conteudoChamada")
    if not content:
        return [], False

    chamadas = []
    for item in content.find_all("div", class_="item"):
        h3 = item.find("h3")
        if not h3:
            continue
        a = h3.find("a", href=True)
        if not a:
            continue

        href = a["href"]
        titulo = a.get_text(strip=True)
        link = f"{BASE_URL}{href}" if href.startswith("/") else href

        # Extrai ID numérico da URL: /chamadas-publicas/chamadapublica/729 → "729"
        m = re.search(r"/chamadapublica/(\d+)", href)
        chamada_id = m.group(1) if m else href.split("/")[-1]

        chamadas.append({
            "chamada_id": chamada_id,
            "titulo": titulo,
            "link": link,
        })

    # Verifica próxima página
    next_start = start + ITEMS_PER_PAGE
    pag = soup.find(class_=lambda x: x and "pagin" in x.lower())
    has_next = bool(pag and any(
        f"start={next_start}" in (a.get("href") or "")
        for a in pag.find_all("a", href=True)
    )) if pag else False

    return chamadas, has_next


# =============================================================================
# PARSING DA PÁGINA DE DETALHE
# =============================================================================

def parse_detail_page(soup: BeautifulSoup, chamada_id: str) -> list[dict]:
    """
    Extrai todos os PDFs da tabela de Documentos.

    Retorna lista de dicts:
        {nome_documento, data_publicacao, url, filename}
    """
    table = soup.find("table", class_="document")
    if not table:
        # Tenta encontrar qualquer tabela com a ID padrão
        table = soup.find("table", id=re.compile(f"documentos-item-{chamada_id}"))
    if not table:
        return []

    tbody = table.find("tbody") or table
    pdfs = []

    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        data_pub = cells[0].get_text(strip=True)
        nome_doc = cells[1].get_text(strip=True)

        # Procura link PDF em "Formatos proprietários" (col 2) e "Formatos Abertos" (col 3)
        pdf_link = cells[2].find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if not pdf_link and len(cells) > 3:
            pdf_link = cells[3].find("a", href=lambda h: h and h.lower().endswith(".pdf"))

        if not pdf_link:
            continue

        href = pdf_link["href"]
        url = urljoin(BASE_URL, href)

        # Nome do arquivo: slug do documento + nome original do arquivo
        filename = href.split("/")[-1]

        pdfs.append({
            "nome_documento": nome_doc,
            "data_publicacao": data_pub,
            "url": url,
            "filename": filename,
        })

    return pdfs


# =============================================================================
# DOWNLOAD DE PDFs
# =============================================================================

def download_pdf(url: str, dest: Path) -> bool:
    """
    Baixa um PDF e salva em dest. Retorna True se bem-sucedido.
    Pula se o arquivo já existir.
    """
    if dest.exists():
        print(f"      [SKIP] {dest.name} — já existe")
        return True

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if resp.status_code != 200:
            print(f"      [ERRO {resp.status_code}] {url}")
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=16384):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                print(f"      [SKIP] {dest.name} — tamanho > 50 MB")
                return False

        dest.write_bytes(b"".join(chunks))
        print(f"      [OK] {dest.name} ({total / 1024:.0f} KB)")
        return True

    except Exception as e:
        print(f"      [ERRO] {dest.name}: {e}")
        return False


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Baixa PDFs das chamadas FINEP")
    parser.add_argument(
        "--max-pages", type=int, default=0,
        help="Máximo de páginas da listagem (0 = todas)"
    )
    parser.add_argument(
        "--situacao", default="encerrada",
        help="Filtro de situação: 'encerrada', 'aberta', '' (todas). Default: encerrada"
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    print("=" * 70)
    print(f"FINEP PDF Downloader — situacao={args.situacao!r}")
    print(f"Saída: {OUTPUT_DIR.resolve()}")
    print(f"Manifest: {MANIFEST_FILE.resolve()}")
    print("=" * 70)

    total_chamadas = 0
    total_pdfs_novos = 0
    total_pdfs_skip = 0
    page = 0

    while True:
        start = page * ITEMS_PER_PAGE
        chamadas, has_next = list_chamadas_page(args.situacao, start)

        if not chamadas:
            print("  Sem chamadas na página — encerrando.")
            break

        for chamada in chamadas:
            cid = chamada["chamada_id"]
            titulo = chamada["titulo"]
            link = chamada["link"]

            print(f"\n  [{cid}] {titulo[:70]}")

            # Busca detalhe
            soup = fetch_html(link)
            if not soup:
                continue

            # Extrai metadados HTML (resumo da chamada)
            item_fields = soup.find("div", class_="item_fields")
            html_meta = ""
            if item_fields:
                for tag in item_fields.find_all(["script", "style"]):
                    tag.decompose()
                html_meta = " ".join(item_fields.get_text(" ", strip=True).split())[:1000]

            pdfs_info = parse_detail_page(soup, cid)
            print(f"    {len(pdfs_info)} PDF(s) encontrado(s)")

            chamada_dir = OUTPUT_DIR / cid
            downloaded = []

            for pdf in pdfs_info:
                dest = chamada_dir / pdf["filename"]
                already_existed = dest.exists()
                time.sleep(SLEEP_BETWEEN_PDFS)

                ok = download_pdf(pdf["url"], dest)
                downloaded.append({
                    "nome_documento": pdf["nome_documento"],
                    "data_publicacao": pdf["data_publicacao"],
                    "url_original": pdf["url"],
                    "arquivo_local": str(dest.relative_to(Path("."))),
                    "baixado": ok,
                })
                if ok and not already_existed:
                    total_pdfs_novos += 1
                else:
                    total_pdfs_skip += 1

            # Atualiza manifest
            manifest[cid] = {
                "chamada_id": cid,
                "titulo": titulo,
                "link": link,
                "html_meta": html_meta,
                "data_extracao": datetime.now().strftime("%Y-%m-%d"),
                "pdfs": downloaded,
            }
            save_manifest(manifest)
            total_chamadas += 1
            time.sleep(SLEEP_BETWEEN_CHAMADAS)

        page += 1
        if not has_next:
            print("\n  Última página atingida.")
            break
        if args.max_pages and page >= args.max_pages:
            print(f"\n  Limite de {args.max_pages} página(s) atingido.")
            break

    print("\n" + "=" * 70)
    print("RESUMO")
    print("=" * 70)
    print(f"  Chamadas processadas : {total_chamadas}")
    print(f"  PDFs baixados        : {total_pdfs_novos}")
    print(f"  PDFs já existentes   : {total_pdfs_skip}")
    print(f"  Manifest salvo em    : {MANIFEST_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()
