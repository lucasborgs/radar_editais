"""
Auditoria da estrutura de editais FINEP — últimos 10 anos.

Coleta 5 editais por ano (2016–2025), baixa o PDF principal de cada um,
parseia as seções e gera um relatório comparativo.

Usage:
    python scripts/audit_finep_structure.py
"""

import io
import json
import re
import time
import logging
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
AUDIT_DIR = ROOT / "audit_finep_structure"
PDFS_DIR = AUDIT_DIR / "pdfs"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# FINEP listing URLs
ENCERRADAS_URL = "http://www.finep.gov.br/chamadas-publicas/chamadaspublicas"
ENCERRADAS_PARAMS = "?situacao=encerrada&filter_order=ordering&filter_order_Dir=desc"
ABERTAS_URL = "http://www.finep.gov.br/chamadas-publicas/chamadaspublicas?situacao=aberta"
ITEMS_PER_PAGE = 10

# Anos alvo
TARGET_YEARS = list(range(2016, 2026))  # 2016 a 2025
TARGET_PER_YEAR = 5


# =============================================================================
# ETAPA 1: Coleta de URLs (scraping da listagem)
# =============================================================================

def fetch_soup(url: str, timeout: int = 20) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return BeautifulSoup(resp.text, "html.parser")


def parse_listing_page(soup: BeautifulSoup) -> list[dict]:
    """Extrai chamadas de uma página de listagem FINEP."""
    content_div = soup.find("div", id="conteudoChamada")
    if not content_div:
        return []

    items = content_div.find_all("div", class_="item")
    results = []
    date_re = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")

    for item in items:
        header = item.find("h3")
        if not header:
            continue
        title_tag = header.find("a")
        if not title_tag:
            continue

        title = " ".join(title_tag.get_text().strip().split())
        href = title_tag.get("href", "")
        link = f"http://www.finep.gov.br{href}" if href.startswith("/") else href

        # Extrair data de publicação
        data_pub_div = item.find("div", class_="data_pub")
        pub_date = None
        pub_year = None
        if data_pub_div:
            span = data_pub_div.find("span")
            if span:
                m = date_re.search(span.get_text())
                if m:
                    pub_date = m.group(1)
                    try:
                        pub_year = datetime.strptime(pub_date, "%d/%m/%Y").year
                    except ValueError:
                        pass

        # Extrair prazo
        prazo_div = item.find("div", class_="prazo")
        prazo = None
        if prazo_div:
            span = prazo_div.find("span")
            if span:
                m = date_re.search(span.get_text())
                if m:
                    prazo = m.group(1)

        results.append({
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "pub_year": pub_year,
            "prazo": prazo,
        })

    return results


def collect_chamadas(max_pages: int = 40) -> list[dict]:
    """Coleta todas as chamadas (abertas + encerradas) com paginação."""
    all_items = []
    seen_links = set()

    # Abertas primeiro
    logger.info("Coletando chamadas abertas...")
    try:
        soup = fetch_soup(ABERTAS_URL)
        for item in parse_listing_page(soup):
            if item["link"] not in seen_links:
                seen_links.add(item["link"])
                all_items.append(item)
        logger.info(f"  → {len(all_items)} abertas")
    except Exception as e:
        logger.warning(f"  Erro nas abertas: {e}")

    # Encerradas com paginação
    logger.info(f"Coletando encerradas ({max_pages} páginas)...")
    for page in range(max_pages):
        start = page * ITEMS_PER_PAGE
        url = f"{ENCERRADAS_URL}{ENCERRADAS_PARAMS}&start={start}"

        try:
            soup = fetch_soup(url)
            items = parse_listing_page(soup)
            if not items:
                logger.info(f"  Página {page + 1}: sem itens — parando")
                break

            new = 0
            for item in items:
                if item["link"] not in seen_links:
                    seen_links.add(item["link"])
                    all_items.append(item)
                    new += 1

            logger.info(f"  Página {page + 1}: {new} novos (total: {len(all_items)})")

            if len(items) < ITEMS_PER_PAGE:
                break

            time.sleep(1)  # throttle

        except Exception as e:
            logger.warning(f"  Erro na página {page + 1}: {e}")
            break

    return all_items


def select_by_year(chamadas: list[dict]) -> dict[int, list[dict]]:
    """Seleciona TARGET_PER_YEAR chamadas por ano alvo."""
    by_year: dict[int, list[dict]] = {y: [] for y in TARGET_YEARS}

    for ch in chamadas:
        y = ch.get("pub_year")
        if y in by_year and len(by_year[y]) < TARGET_PER_YEAR:
            by_year[y].append(ch)

    return by_year


# =============================================================================
# ETAPA 2: Busca de PDFs nas páginas de detalhe
# =============================================================================

def find_all_pdfs(soup: BeautifulSoup) -> list[dict]:
    """Encontra TODOS os PDFs na tabela de documentos da página de detalhe."""
    table = soup.find("table", class_="document")
    if not table:
        return []

    tbody = table.find("tbody") or table
    rows = tbody.find_all("tr")
    pdfs = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        doc_name = cells[1].get_text(strip=True)
        pdf_link = cells[2].find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if not pdf_link:
            # Tenta na célula 0 também
            pdf_link = cells[0].find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if not pdf_link:
            continue

        href = pdf_link.get("href", "")
        url = f"http://www.finep.gov.br{href}" if href.startswith("/") else href

        pdfs.append({"nome": doc_name, "url": url})

    return pdfs


def pick_main_pdf(pdfs: list[dict]) -> dict | None:
    """Seleciona o PDF principal (regulamento > edital > chamada > primeiro)."""
    PRIORITY = ["regulamento", "edital", "chamada"]
    best = None
    best_rank = len(PRIORITY)

    for pdf in pdfs:
        name_lower = pdf["nome"].lower()
        rank = len(PRIORITY)
        for i, kw in enumerate(PRIORITY):
            if kw in name_lower:
                rank = i
                break
        if rank < best_rank:
            best_rank = rank
            best = pdf

    return best or (pdfs[0] if pdfs else None)


def download_pdf(url: str, dest: Path, max_mb: int = 15) -> bool:
    """Baixa um PDF para o path indicado."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if resp.status_code != 200:
            return False

        chunks = []
        total = 0
        max_bytes = max_mb * 1024 * 1024
        for chunk in resp.iter_content(chunk_size=8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                logger.warning(f"  PDF excede {max_mb}MB — truncando")
                break

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"".join(chunks))
        return True

    except Exception as e:
        logger.warning(f"  Erro ao baixar {url}: {e}")
        return False


# =============================================================================
# ETAPA 3: Parse de seções do PDF
# =============================================================================

def extract_pdf_text(pdf_path: Path) -> str:
    """Extrai texto de um PDF local com pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber não instalado")
        return ""

    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
        return "\n".join(text_parts)
    except Exception as e:
        logger.warning(f"  Erro ao parsear {pdf_path.name}: {e}")
        return ""


def detect_sections(text: str) -> list[dict]:
    """Detecta seções numeradas no texto do edital.

    Patterns detectados:
      - "1. DO OBJETO" / "1 - DO OBJETO" / "1 – DO OBJETO"
      - "1.1. Da Elegibilidade"
      - "CAPÍTULO I - DO OBJETO"
      - Headers em CAPS sem número
    """
    lines = text.split("\n")
    sections = []

    # Pattern 1: seções numeradas "N. TÍTULO" ou "N - TÍTULO"
    numbered_re = re.compile(
        r"^\s*(\d+\.?\d*\.?)\s*[.\-–—]\s*(.+)$"
    )

    # Pattern 2: "CAPÍTULO N" ou "SEÇÃO N"
    chapter_re = re.compile(
        r"^\s*(CAP[ÍI]TULO|SE[ÇC][ÃA]O)\s+([IVXLCDM]+|\d+)\s*[.\-–—]?\s*(.*)$",
        re.IGNORECASE,
    )

    # Pattern 3: linha inteira em CAPS (mín 3 palavras, sem números no início)
    caps_re = re.compile(
        r"^[A-ZÁÀÂÃÉÈÊÍÏÓÔÕÚÜÇ\s,]{15,}$"
    )

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped or len(line_stripped) < 5:
            continue

        section = None

        # Tenta pattern numerado
        m = numbered_re.match(line_stripped)
        if m:
            num = m.group(1).rstrip(".")
            title = m.group(2).strip()
            # Filtra falsos positivos (linhas com número mas sem título tipo seção)
            if len(title) > 3 and not title[0].isdigit():
                # Aceita seções de nível 1 (ex: "1", "2") e nível 2 (ex: "1.1", "2.3")
                if num.count(".") <= 1:
                    section = {
                        "line_num": i,
                        "number": num,
                        "title": title,
                        "pattern": "numbered",
                    }

        # Tenta pattern capítulo
        if not section:
            m = chapter_re.match(line_stripped)
            if m:
                section = {
                    "line_num": i,
                    "number": f"{m.group(1)} {m.group(2)}",
                    "title": m.group(3).strip() or line_stripped,
                    "pattern": "chapter",
                }

        # Tenta CAPS header (somente se for provável título de seção)
        if not section and caps_re.match(line_stripped):
            # Ignora linhas que parecem ser cabeçalho de página ou assinatura
            lower = line_stripped.lower()
            if any(kw in lower for kw in ["ministério", "finep", "página", "cnpj", "endereço"]):
                continue
            if len(line_stripped.split()) >= 3:
                section = {
                    "line_num": i,
                    "number": "",
                    "title": line_stripped,
                    "pattern": "caps_header",
                }

        if section:
            sections.append(section)

    # Extrai conteúdo entre seções
    for idx, sec in enumerate(sections):
        start_line = sec["line_num"] + 1
        end_line = sections[idx + 1]["line_num"] if idx + 1 < len(sections) else len(lines)
        content = "\n".join(lines[start_line:end_line]).strip()
        sec["content_preview"] = content[:300]
        sec["char_count"] = len(content)

    return sections


def classify_section(title: str) -> str:
    """Classifica o tipo da seção baseado no título."""
    t = title.lower()

    patterns = {
        "OBJETO": r"objeto|finalidade|propósito",
        "OBJETIVOS": r"objetivo",
        "ELEGIBILIDADE": r"elegibil|participan|proponen|habilitaç|credenciament",
        "RECURSOS": r"recurso|orçament|financ|valor|dotaç",
        "CRONOGRAMA": r"cronograma|prazo|calendário|etapa|fase",
        "AVALIACAO": r"avaliaç|seleç|mérito|critério|julgament|classificaç",
        "CONTRATACAO": r"contrataç|convênio|ajuste|instrumento jurídic",
        "PRESTACAO_CONTAS": r"prestaç|acompanhament|monitorament|fiscalizaç",
        "PROPRIEDADE_INTELECTUAL": r"propriedade intelectual|patente|direito autoral",
        "DISPOSICOES_GERAIS": r"disposiç|gera[il]|fina[il]|transit",
        "DEFINICOES": r"definiç|glossário|conceito",
        "CONTRAPARTIDA": r"contrapartida",
        "EQUIPE": r"equipe|pessoal|recursos humanos",
        "RESULTADOS": r"resultado|entrega|produto|deliverable",
        "PENALIDADES": r"penalidade|sançã|infração|inadimplên",
    }

    for section_type, pattern in patterns.items():
        if re.search(pattern, t):
            return section_type

    return "OUTRO"


# =============================================================================
# MAIN
# =============================================================================

def main():
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    PDFS_DIR.mkdir(parents=True, exist_ok=True)

    manifest_path = AUDIT_DIR / "manifest.json"

    # ── Etapa 1: Coleta de URLs ─────────────────────────────────────────────
    if manifest_path.exists():
        logger.info("Manifest já existe — carregando...")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        logger.info("=== ETAPA 1: Coletando URLs de chamadas FINEP ===")
        all_chamadas = collect_chamadas(max_pages=40)
        logger.info(f"Total coletado: {len(all_chamadas)} chamadas")

        by_year = select_by_year(all_chamadas)

        manifest = {"collected_at": datetime.now().isoformat(), "by_year": {}}
        for year in TARGET_YEARS:
            items = by_year[year]
            manifest["by_year"][str(year)] = items
            logger.info(f"  {year}: {len(items)} editais selecionados")

        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Manifest salvo em {manifest_path}")

    # ── Etapa 2: Baixar PDFs ────────────────────────────────────────────────
    logger.info("\n=== ETAPA 2: Baixando PDFs dos editais ===")
    total_selected = sum(len(v) for v in manifest["by_year"].values())
    downloaded = 0
    failed = 0

    for year_str, items in manifest["by_year"].items():
        for item in items:
            link = item.get("link", "")
            title_short = item.get("title", "")[:60]

            # Verifica se já tem PDF info
            if item.get("pdf_downloaded"):
                downloaded += 1
                continue

            if not link:
                item["pdf_error"] = "sem link"
                failed += 1
                continue

            logger.info(f"  [{year_str}] {title_short}...")

            try:
                soup = fetch_soup(link)
                time.sleep(1.5)
            except Exception as e:
                item["pdf_error"] = str(e)
                failed += 1
                logger.warning(f"    Erro ao acessar detalhe: {e}")
                continue

            # Encontrar PDFs
            all_pdfs = find_all_pdfs(soup)
            item["all_pdfs"] = [p["nome"] for p in all_pdfs]

            main_pdf = pick_main_pdf(all_pdfs)
            if not main_pdf:
                item["pdf_error"] = "nenhum PDF encontrado na página"
                failed += 1
                logger.warning(f"    Nenhum PDF encontrado")
                continue

            item["main_pdf_name"] = main_pdf["nome"]
            item["main_pdf_url"] = main_pdf["url"]

            # Extrair chamada_id do link
            chamada_id = link.rstrip("/").split("/")[-1]
            pdf_filename = re.sub(r"[^\w\-.]", "_", main_pdf["nome"])[:80] + ".pdf"
            pdf_path = PDFS_DIR / f"{year_str}_{chamada_id}_{pdf_filename}"

            if pdf_path.exists():
                item["pdf_path"] = str(pdf_path)
                item["pdf_downloaded"] = True
                downloaded += 1
                logger.info(f"    PDF já existe: {pdf_path.name}")
                continue

            ok = download_pdf(main_pdf["url"], pdf_path)
            if ok:
                item["pdf_path"] = str(pdf_path)
                item["pdf_downloaded"] = True
                downloaded += 1
                logger.info(f"    ✓ {pdf_path.name} ({pdf_path.stat().st_size // 1024}KB)")
            else:
                item["pdf_error"] = "download falhou"
                failed += 1

            time.sleep(1.5)

    # Salva manifest atualizado
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"PDFs: {downloaded} baixados, {failed} falhas (de {total_selected} selecionados)")

    # ── Etapa 3: Parse de seções ────────────────────────────────────────────
    logger.info("\n=== ETAPA 3: Parseando estrutura de seções ===")
    results = {}

    for year_str, items in manifest["by_year"].items():
        for item in items:
            pdf_path_str = item.get("pdf_path")
            if not pdf_path_str:
                continue

            pdf_path = Path(pdf_path_str)
            if not pdf_path.exists():
                continue

            title_short = item.get("title", "")[:60]
            logger.info(f"  [{year_str}] {title_short}")

            text = extract_pdf_text(pdf_path)
            if not text:
                logger.warning(f"    Texto vazio")
                continue

            sections = detect_sections(text)
            for sec in sections:
                sec["section_type"] = classify_section(sec["title"])

            item["n_pages"] = text.count("\n\n") // 2  # estimativa grosseira
            item["n_chars"] = len(text)
            item["n_sections_detected"] = len(sections)
            item["sections"] = [
                {
                    "number": s["number"],
                    "title": s["title"],
                    "section_type": s["section_type"],
                    "pattern": s["pattern"],
                    "char_count": s["char_count"],
                }
                for s in sections
            ]

            key = f"{year_str}_{item.get('title', '')[:50]}"
            results[key] = {
                "year": int(year_str),
                "title": item["title"],
                "main_pdf": item.get("main_pdf_name"),
                "n_chars": len(text),
                "sections": item["sections"],
            }

            level1_types = [
                s["section_type"]
                for s in sections
                if s["pattern"] in ("numbered", "chapter") and "." not in s.get("number", "")
            ]
            logger.info(f"    {len(sections)} seções → tipos L1: {level1_types}")

    # Salva manifest final e resultados
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    results_path = AUDIT_DIR / "section_analysis.json"
    results_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── Etapa 4: Relatório ──────────────────────────────────────────────────
    logger.info("\n=== RELATÓRIO: Estrutura de Editais FINEP (2016–2025) ===\n")

    # Contagem de seções por tipo, por ano
    type_by_year: dict[int, dict[str, int]] = {}
    all_types_seen: set[str] = set()

    for key, data in results.items():
        year = data["year"]
        if year not in type_by_year:
            type_by_year[year] = {}

        for sec in data["sections"]:
            st = sec["section_type"]
            if sec["pattern"] in ("numbered", "chapter"):
                all_types_seen.add(st)
                type_by_year[year][st] = type_by_year[year].get(st, 0) + 1

    # Frequência global de cada tipo
    global_freq: dict[str, int] = {}
    for year_counts in type_by_year.values():
        for st, count in year_counts.items():
            global_freq[st] = global_freq.get(st, 0) + count

    sorted_types = sorted(global_freq.items(), key=lambda x: -x[1])

    print("\n┌─────────────────────────────────────────────┐")
    print("│  Tipos de seção mais frequentes (global)    │")
    print("├─────────────────────────────┬───────────────┤")
    print(f"│ {'Tipo':<27} │ {'Ocorrências':>13} │")
    print("├─────────────────────────────┼───────────────┤")
    for st, count in sorted_types:
        print(f"│ {st:<27} │ {count:>13} │")
    print("└─────────────────────────────┴───────────────┘")

    # Estrutura por edital
    print("\n\nDetalhe por edital:")
    print("=" * 100)
    for key, data in sorted(results.items()):
        year = data["year"]
        title = data["title"][:70]
        n_chars = data["n_chars"]
        secs = data["sections"]
        top_level = [
            s for s in secs
            if s["pattern"] in ("numbered", "chapter") and "." not in s.get("number", "")
        ]

        print(f"\n[{year}] {title}")
        print(f"  PDF: {data.get('main_pdf', '?')} | {n_chars:,} chars | {len(secs)} seções detectadas")
        if top_level:
            print(f"  Seções de nível 1:")
            for s in top_level:
                print(f"    {s['number']:>4}  {s['title']:<50}  → {s['section_type']}")
        else:
            print(f"  (Sem seções numeradas de nível 1 detectadas)")

    # Salva relatório
    report_path = AUDIT_DIR / "report.txt"
    logger.info(f"\nRelatório salvo em {report_path}")


if __name__ == "__main__":
    main()
