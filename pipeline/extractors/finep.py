"""
Scraper para chamadas públicas da FINEP.

Modo produção (padrão):
  Acessa http://www.finep.gov.br/chamadas-publicas/chamadas-publicas
  → captura editais abertos + encerrados recentes listados na página principal.
  → para cada chamada, busca página de detalhe (descrição, PDFs).
  → baixa todos os PDFs para bronze_data/finep_pdfs/{chamada_id}/.

Modo histórico (include_historical=True):
  Acessa chamadaspublicas?situacao=encerrada com paginação (&start=N, 10/pág).
  Existem ~34 páginas de encerradas (desde 2001).
  Por padrão, faz max_pages=20 páginas (≈200 chamadas encerradas).
"""
import io
import re
import time
from pathlib import Path

from .base import BaseScraper

from config import FINEP_PDFS_DIR


class FINEPScraper(BaseScraper):
    """Scraper para editais da FINEP."""

    BASE_URL = "http://www.finep.gov.br/chamadas-publicas/chamadas-publicas"

    # URL com todas as chamadas abertas (listagem completa)
    ABERTAS_URL = "http://www.finep.gov.br/chamadas-publicas/chamadaspublicas?situacao=aberta"

    # URL da listagem de encerradas (usada no modo histórico)
    ENCERRADAS_URL = "http://www.finep.gov.br/chamadas-publicas/chamadaspublicas"
    ENCERRADAS_PARAMS = "?situacao=encerrada&filter_order=ordering&filter_order_Dir=asc"

    ITEMS_PER_PAGE = 10
    THROTTLE_SECONDS = 2

    def __init__(self):
        super().__init__(source_name="FINEP", output_subdir="finep_raw")
        self._date_re = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")

    def extract(self, include_historical: bool = False, max_pages: int = 20) -> list:
        """Extrai chamadas públicas da FINEP.

        Args:
            include_historical: Se True, acessa a listagem de encerradas com
                                paginação completa + busca descrição nas páginas
                                de detalhe de cada chamada.
            max_pages:          Número máximo de páginas de encerradas a buscar
                                (cada página tem 10 chamadas). Default: 20 (≈200 chamadas).
        """
        all_results = []
        seen_links: set = set()

        # Página principal (abertos + recentes)
        for item in self._scrape_page(self.BASE_URL):
            link = item.get("link", "")
            if link not in seen_links:
                seen_links.add(link)
                all_results.append(item)

        # Listagem completa de chamadas abertas (com paginação)
        for item in self._scrape_paginated(self.ABERTAS_URL, max_pages=50):
            link = item.get("link", "")
            if link not in seen_links:
                seen_links.add(link)
                all_results.append(item)

        # Enriquece cada chamada com detalhe + PDFs
        print(f"\nEnriquecendo {len(all_results)} chamadas (detalhe + PDFs)...")
        for i, chamada in enumerate(all_results, 1):
            print(f"  [{i}/{len(all_results)}] {chamada['titulo'][:60]}...")
            self._enrich_chamada(chamada)
            time.sleep(self.THROTTLE_SECONDS)

        if include_historical:
            print(f"\nModo histórico FINEP: buscando encerradas ({max_pages} páginas × {self.ITEMS_PER_PAGE}/pág)...")
            for item in self._scrape_encerradas(max_pages=max_pages):
                link = item.get("link", "")
                if link not in seen_links:
                    seen_links.add(link)
                    all_results.append(item)

        if all_results:
            prefix = "finep_historical" if include_historical else "finep_chamadas"
            self._save(all_results, prefix=prefix)

        return all_results

    # =========================================================================
    # Enriquecimento: detalhe + PDFs
    # =========================================================================

    def _enrich_chamada(self, chamada: dict) -> None:
        """Busca página de detalhe, extrai descrição, PDFs e baixa para disco."""
        url = chamada.get("link", "")
        if not url:
            return

        soup = self._fetch_detail_soup(url)
        if not soup:
            return

        # Descrição e HTML bruto
        chamada["descricao"] = self._get_html_description(soup)
        chamada["raw_html"] = self._get_raw_html_content(soup)

        # Todas as URLs de PDFs da tabela de documentos
        pdf_urls = self._find_all_pdf_urls(soup)
        chamada["pdf_urls"] = pdf_urls

        # Download de PDFs para disco
        chamada_id = self._extract_chamada_id(url)
        chamada["chamada_id"] = chamada_id

        if pdf_urls and chamada_id:
            pdf_texts = self._download_all_pdfs(chamada_id, pdf_urls)
            chamada["pdf_texts"] = pdf_texts
        else:
            chamada["pdf_texts"] = {}

    def _extract_chamada_id(self, url: str) -> str:
        """Extrai ID numérico da URL da chamada.

        Ex: http://www.finep.gov.br/chamadas-publicas/chamadapublica/782 → '782'
        """
        m = re.search(r"/chamadapublica/(\d+)", url)
        return m.group(1) if m else ""

    def _find_all_pdf_urls(self, soup) -> list[dict]:
        """Encontra TODOS os PDFs na tabela de documentos da página de detalhe.

        Returns:
            Lista de dicts: [{"nome": "Edital", "url": "http://..."}, ...]
        """
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

            # Tenta na coluna de formatos (índice 2), depois na coluna 0
            pdf_link = cells[2].find("a", href=lambda h: h and h.lower().endswith(".pdf"))
            if not pdf_link:
                pdf_link = cells[0].find("a", href=lambda h: h and h.lower().endswith(".pdf"))
            if not pdf_link:
                continue

            href = pdf_link.get("href", "")
            url = f"http://www.finep.gov.br{href}" if href.startswith("/") else href

            pdfs.append({"nome": doc_name, "url": url})

        return pdfs

    def _download_all_pdfs(self, chamada_id: str, pdf_urls: list[dict]) -> dict[str, str]:
        """Baixa todos os PDFs de uma chamada para disco e extrai texto.

        Args:
            chamada_id: ID numérico da chamada (ex: "782")
            pdf_urls: Lista de {"nome": ..., "url": ...}

        Returns:
            Dict nome_slug → texto extraído do PDF.
        """
        dest_dir = FINEP_PDFS_DIR / chamada_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        pdf_texts = {}

        for pdf_info in pdf_urls:
            nome = pdf_info["nome"]
            url = pdf_info["url"]

            # Slug para nome de arquivo
            slug = self._slugify(nome)
            dest_path = dest_dir / f"{slug}.pdf"

            # Skip se já existe (cache em disco)
            if dest_path.exists() and dest_path.stat().st_size > 0:
                print(f"    PDF já existe: {dest_path.name}")
                text = self._extract_pdf_from_file(dest_path)
            else:
                print(f"    Baixando: {nome} → {dest_path.name}")
                ok = self._download_pdf(url, dest_path)
                if ok:
                    text = self._extract_pdf_from_file(dest_path)
                else:
                    text = ""

            if text:
                pdf_texts[slug] = text

        return pdf_texts

    def _download_pdf(self, url: str, dest: Path, max_mb: int = 15) -> bool:
        """Baixa um PDF para o path indicado."""
        import requests

        try:
            resp = requests.get(url, headers=self.headers, timeout=30, stream=True)
            if resp.status_code != 200:
                print(f"    Aviso: HTTP {resp.status_code} para {url}")
                return False

            chunks = []
            total = 0
            max_bytes = max_mb * 1024 * 1024
            for chunk in resp.iter_content(chunk_size=8192):
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    print(f"    Aviso: PDF excede {max_mb}MB — truncando")
                    break

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"".join(chunks))
            return True

        except Exception as e:
            print(f"    Erro ao baixar {url}: {e}")
            return False

    def _extract_pdf_from_file(self, pdf_path: Path) -> str:
        """Extrai texto de um PDF local com pdfplumber."""
        try:
            import pdfplumber
        except ImportError:
            print("    Aviso: pdfplumber não instalado — instale com 'pip install pdfplumber'")
            return ""

        try:
            text_parts = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts)
        except Exception as e:
            print(f"    Aviso: falha ao parsear {pdf_path.name}: {e}")
            return ""

    def _slugify(self, name: str) -> str:
        """Converte nome de documento em slug seguro para filename."""
        import unicodedata
        # NFD decompose + strip combining marks
        s = unicodedata.normalize("NFD", name)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        s = s.lower().strip()
        s = re.sub(r"[^\w\s-]", "", s)
        s = re.sub(r"[\s_-]+", "_", s)
        return s[:80] or "documento"

    # =========================================================================
    # Scraping da página principal
    # =========================================================================

    def _scrape_page(self, url: str) -> list:
        """Extrai chamadas de uma URL de listagem da FINEP."""
        print(f"Acessando: {url}")
        try:
            soup = self._fetch_page(url, timeout=15)
        except Exception as e:
            print(f"Erro ao acessar {url}: {e}")
            return []

        content_div = soup.find("div", id="conteudoChamada")
        if not content_div:
            print(f"AVISO: Container 'div#conteudoChamada' não encontrado em {url}")
            return []

        items = content_div.find_all("div", class_="item")
        print(f"Encontrados {len(items)} candidatos a editais.")

        chamadas = []
        for item in items:
            try:
                chamada = self._parse_item(item)
                if chamada:
                    chamadas.append(chamada)
            except Exception as e:
                print(f"Erro ao processar item: {e}")
        return chamadas

    def _scrape_paginated(self, base_url: str, max_pages: int = 50) -> list:
        """Percorre uma listagem FINEP com paginação (&start=N)."""
        all_items = []
        for page_num in range(max_pages):
            start = page_num * self.ITEMS_PER_PAGE
            sep = "&" if "?" in base_url else "?"
            url = f"{base_url}{sep}start={start}" if start > 0 else base_url
            items = self._scrape_page(url)
            if not items:
                break
            all_items.extend(items)
            if len(items) < self.ITEMS_PER_PAGE:
                break  # última página
        return all_items

    # =========================================================================
    # Scraping das encerradas (modo histórico)
    # =========================================================================

    def _scrape_encerradas(self, max_pages: int = 20) -> list:
        """Percorre a listagem de encerradas página a página."""
        all_items = []

        for page_num in range(max_pages):
            start = page_num * self.ITEMS_PER_PAGE
            url = f"{self.ENCERRADAS_URL}{self.ENCERRADAS_PARAMS}&start={start}"
            print(f"  [Pág {page_num + 1}/{max_pages}] {url}")

            try:
                soup = self._fetch_page(url, timeout=20)
            except Exception as e:
                print(f"  Erro na página {page_num + 1}: {e} — parando")
                break

            content_div = soup.find("div", id="conteudoChamada")
            if not content_div:
                print(f"  Container não encontrado — parando")
                break

            items = content_div.find_all("div", class_="item")
            if not items:
                print(f"  Sem itens na página {page_num + 1} — parando")
                break

            page_items = []
            for item in items:
                try:
                    chamada = self._parse_item(item, default_status="ENCERRADA")
                    if chamada:
                        page_items.append(chamada)
                except Exception as e:
                    print(f"  Erro ao parsear item: {e}")

            # Enriquece cada chamada com detalhe + PDFs
            for chamada in page_items:
                self._enrich_chamada(chamada)
                time.sleep(self.THROTTLE_SECONDS)

            all_items.extend(page_items)
            print(f"  → {len(page_items)} itens | total acumulado: {len(all_items)}")

            # Verifica se existe próxima página
            if not self._has_next_page(soup, start):
                print(f"  Última página atingida na pág {page_num + 1}.")
                break

        return all_items

    def _has_next_page(self, soup, current_start: int) -> bool:
        """Verifica se há próxima página na paginação."""
        pag = soup.find(class_=lambda x: x and "pagin" in x.lower())
        if not pag:
            return False
        next_start = current_start + self.ITEMS_PER_PAGE
        return any(
            f"start={next_start}" in (a.get("href") or "")
            for a in pag.find_all("a", href=True)
        )

    # =========================================================================
    # Extração de detalhe: HTML + PDF do Regulamento
    # =========================================================================

    def _fetch_detail_soup(self, url: str):
        """Faz o fetch da página de detalhe e retorna o BeautifulSoup (ou None)."""
        if not url:
            return None
        try:
            return self._fetch_page(url, timeout=20)
        except Exception as e:
            print(f"    Aviso: falha ao acessar detalhe {url}: {e}")
            return None

    def _get_html_description(self, soup) -> str:
        """Extrai texto da div.item_fields (metadados estruturados da página de detalhe)."""
        el = soup.find("div", class_="item_fields")
        if not el:
            return ""
        for tag in el.find_all(["script", "style"]):
            tag.decompose()
        return " ".join(el.get_text(" ", strip=True).split())

    def _get_raw_html_content(self, soup) -> str:
        """Retorna o HTML bruto do elemento de conteúdo principal."""
        el = soup.find("div", class_="item_fields")
        if el:
            for tag in el.find_all(["script", "style"]):
                tag.decompose()
            return el.decode_contents()
        return ""

    def _extract_pdf_text(self, pdf_url: str) -> str:
        """Baixa um PDF e extrai texto via pdfplumber. Retorna '' em caso de falha."""
        try:
            import pdfplumber
        except ImportError:
            print("    Aviso: pdfplumber não instalado — instale com 'pip install pdfplumber'")
            return ""

        try:
            import requests
            resp = requests.get(pdf_url, timeout=20, stream=True)
            if resp.status_code != 200:
                return ""

            chunks, total = [], 0
            MAX_BYTES = 10 * 1024 * 1024  # 10 MB
            for chunk in resp.iter_content(chunk_size=8192):
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_BYTES:
                    break
            pdf_bytes = b"".join(chunks)

            text_parts = []
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text_parts.append(page.extract_text() or "")

            return " ".join(" ".join(t.split()) for t in text_parts)

        except Exception as e:
            print(f"    Aviso: falha ao extrair PDF {pdf_url}: {e}")
            return ""

    # =========================================================================
    # Parse de item individual
    # =========================================================================

    def _parse_item(self, item, default_status: str = None) -> dict:
        """Parseia um <div class='item'> da listagem FINEP."""
        header = item.find("h3")
        if not header:
            return None
        title_tag = header.find("a")
        if not title_tag:
            return None

        titulo = self._clean_text(title_tag.get_text())
        href = title_tag.get("href", "")
        link = f"http://www.finep.gov.br{href}" if href.startswith("/") else href

        data_publicacao = self._extract_span_text(item, "data_pub")
        prazo = self._extract_span_text(item, "prazo")
        fonte_recurso = self._extract_span_text(item, "fonte")
        publico_alvo = self._extract_span_text(item, "publico", span_class="tag")
        tema = self._extract_span_text(item, "tema")

        status = default_status or self._get_status(item)
        chamada_id = self._extract_chamada_id(link)

        return {
            "fonte": self.source_name,
            "chamada_id": chamada_id,
            "titulo": titulo,
            "link": link,
            "status": status,
            "data_publicacao": data_publicacao,
            "prazo_envio": prazo,
            "fonte_recurso": fonte_recurso,
            "publico_alvo": publico_alvo,
            "tema": tema,
            "descricao": "",       # preenchido por _enrich_chamada
            "raw_html": "",        # preenchido por _enrich_chamada
            "pdf_urls": [],        # preenchido por _enrich_chamada
            "pdf_texts": {},       # preenchido por _enrich_chamada
            "data_extracao": self._get_timestamp(),
        }

    def _extract_span_text(self, item, div_class: str, span_class: str = None) -> str:
        """Extrai texto de span dentro de div com a classe dada (suporta multi-class)."""
        div = item.find("div", class_=div_class)
        if not div:
            return None
        span = div.find("span", class_=span_class) if span_class else div.find("span")
        if not span:
            return None
        return self._clean_text(span.get_text())

    def _get_status(self, item) -> str:
        """Determina status a partir da seção pai (apenas na página principal)."""
        parent_section = item.find_parent("div", class_="item-separator-conteudo")
        if not parent_section:
            return "Desconhecido"
        section_header = parent_section.find("h2", class_="header")
        if not section_header:
            return "Desconhecido"
        text = section_header.get_text().strip().lower()
        if "aberta" in text:
            return "ABERTA"
        elif "encerrada" in text:
            return "ENCERRADA"
        elif "resultado" in text:
            return "RESULTADO_DIVULGADO"
        return "Desconhecido"


# Permite execução direta do arquivo
if __name__ == "__main__":
    scraper = FINEPScraper()
    scraper.run()
