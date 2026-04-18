"""
Live Fetcher (Radar de Editais)

Busca o conteúdo completo de um edital a partir de sua URL ao vivo.
Usado pela feature Writing quando o section_index não existe localmente.

Estratégia por etapa:
  1. GET da URL → parse HTML → detecta seções por headers h2/h3/h4
  2. Se seções insuficientes → busca link de PDF (regulamento/edital) na página
  3. GET do PDF → extrai texto com pdfplumber → detecta seções numeradas
  4. Persiste resultado em section_index/{id}.json para reutilização
"""

import io
import json
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import SECTION_INDEX_DIR

logger = logging.getLogger(__name__)

# Seletores de conteúdo principal (tentados em ordem decrescente de especificidade)
_CONTENT_SELECTORS = [
    ("div", {"class": "entry-content"}),   # FAPESP (WordPress)
    ("div", {"id": "conteudo"}),            # FAPESP alternativo
    ("div", {"class": "item_fields"}),      # FINEP
    ("ul",  {"class": "destaques-conteudo"}), # BNDES portal produto
    ("main", {}),
    ("article", {}),
    ("div", {"id": "content"}),
    ("div", {"class": "content"}),
]

# Links de PDF com keywords de documento normativo
_PDF_EXT_RE = re.compile(r"\.pdf(\?[^\"']*)?$", re.I)
_REGULAMENTO_KW_RE = re.compile(
    r"regulamento|edital|chamada|instrução\s*normativa|manual|termo\s*de\s*refer",
    re.I,
)

# Seções numeradas em texto de PDF: "1. Objeto", "2.1 Elegibilidade"
_NUMBERED_SECTION_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)*\.?\s+[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ][^\n]{3,80})"
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RadarEditais/1.0; +https://github.com/radar-editais)",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


class LiveFetcher:
    """
    Busca e estrutura o conteúdo completo de um edital a partir de sua URL.

    Fluxo principal:
        fetch_and_save(edital_id, url)
            → _fetch_html_sections(url)          HTML h2/h3/h4 → seções
            → se poucas seções: _find_pdf_url()  encontra link do regulamento
            → _fetch_pdf_sections(pdf_url)        pdfplumber → seções numeradas
            → _save(edital_id, sections)          persiste em section_index/
    """

    def __init__(self, timeout: int = 30, max_pdf_pages: int = 15):
        self.timeout = timeout
        self.max_pdf_pages = max_pdf_pages
        self._http = requests.Session()
        self._http.headers.update(_HEADERS)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def fetch_text(self, url: str, max_chars: int = 6000) -> tuple[str, str]:
        """
        Faz scraping da URL e retorna texto limpo + título da página.
        Usado pelo ProfileExtractor (não estrutura em seções).

        Returns:
            (text, page_title) — text truncado em max_chars, page_title pode ser ""
        """
        sections, soup = self._fetch_html_sections(url)
        page_title = ""
        if soup:
            title_el = soup.find("title")
            if title_el:
                page_title = title_el.get_text(strip=True)

        if sections:
            text = "\n\n".join(
                f"{s['title']}\n{s['content']}" for s in sections if s.get("content")
            )
        elif soup:
            body = soup.find("body")
            text = body.get_text(" ", strip=True) if body else ""
        else:
            text = ""

        return text[:max_chars], page_title

    def fetch_and_save(self, edital_id: str, url: str) -> list[dict]:
        """
        Busca conteúdo ao vivo, estrutura em seções e persiste no section_index.

        Returns:
            Lista de seções [{title, content}], ou [] em caso de falha total.
        """
        if not url:
            logger.warning(f"[{edital_id}] URL ausente — live fetch ignorado")
            return []

        logger.info(f"[{edital_id}] Live fetch iniciado: {url}")

        # Etapa 1: HTML
        sections, soup = self._fetch_html_sections(url)

        # Etapa 2: PDF (quando HTML retorna menos de 3 seções)
        if len(sections) < 3 and soup is not None:
            pdf_url = self._find_pdf_url(url, soup)
            if pdf_url:
                logger.info(f"[{edital_id}] PDF encontrado: {pdf_url}")
                pdf_sections = self._fetch_pdf_sections(pdf_url)
                if pdf_sections:
                    sections = pdf_sections  # PDF tem prioridade — documento normativo completo

        if not sections:
            logger.warning(f"[{edital_id}] Live fetch não retornou seções")
            return []

        self._save(edital_id, url, sections)
        logger.info(f"[{edital_id}] {len(sections)} seções salvas via live fetch")
        return sections

    # ------------------------------------------------------------------
    # Etapa 1: HTML
    # ------------------------------------------------------------------

    def _fetch_html_sections(self, url: str) -> tuple[list[dict], Optional[object]]:
        """
        Faz GET da URL, extrai seções do HTML.

        Returns:
            (sections, soup) — soup é retornado para reuso na busca de PDF.
        """
        try:
            resp = self._http.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Erro ao acessar {url}: {e}")
            return [], None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove ruído estrutural
        for noise in soup.find_all(["script", "style", "nav", "footer", "header"]):
            noise.decompose()

        # Tenta área de conteúdo principal
        content_el = None
        for tag_name, attrs in _CONTENT_SELECTORS:
            el = soup.find(tag_name, attrs) if attrs else soup.find(tag_name)
            if el and len(el.get_text(strip=True)) > 200:
                content_el = el
                break
        if content_el is None:
            content_el = soup.find("body") or soup

        sections = self._sections_from_headers(content_el)
        if not sections:
            sections = self._sections_from_paragraphs(content_el)

        return sections, soup

    def _sections_from_headers(self, el) -> list[dict]:
        """Divide o conteúdo usando h2/h3/h4 como delimitadores de seção."""
        headers = el.find_all(["h2", "h3", "h4"])
        if len(headers) < 2:
            return []

        sections = []
        for header in headers:
            title = header.get_text(strip=True)
            if not title or len(title) < 3 or len(title) > 150:
                continue

            content_parts = []
            for sib in header.next_siblings:
                if getattr(sib, "name", None) in ["h2", "h3", "h4"]:
                    break
                if hasattr(sib, "get_text"):
                    text = sib.get_text(" ", strip=True)
                    if text:
                        content_parts.append(text)
                elif isinstance(sib, str) and sib.strip():
                    content_parts.append(sib.strip())

            content = " ".join(content_parts).strip()
            if content or len(title) > 10:
                sections.append({"title": title, "content": content})

        return sections if len(sections) >= 2 else []

    def _sections_from_paragraphs(self, el) -> list[dict]:
        """Fallback: agrupa parágrafos em blocos de ~300 chars."""
        paras = [
            p.get_text(" ", strip=True)
            for p in el.find_all("p")
            if len(p.get_text(strip=True)) > 50
        ]
        if not paras:
            text = el.get_text(" ", strip=True)
            return [{"title": "Conteúdo", "content": text}] if len(text) > 100 else []

        sections, buffer = [], ""
        for para in paras:
            buffer = f"{buffer} {para}".strip() if buffer else para
            if len(buffer) >= 300:
                sections.append({"title": "Informações", "content": buffer})
                buffer = ""
        if buffer:
            if sections:
                sections[-1]["content"] += f" {buffer}"
            else:
                sections.append({"title": "Conteúdo", "content": buffer})

        return sections

    # ------------------------------------------------------------------
    # Etapa 2: PDF
    # ------------------------------------------------------------------

    def _find_pdf_url(self, page_url: str, soup) -> Optional[str]:
        """
        Procura link de PDF na página já parseada.
        Prioridade: links com keywords de documento normativo.
        Fallback: primeiro link com extensão .pdf.
        """
        # Prioridade: PDF com keyword normativa no texto do link
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not _PDF_EXT_RE.search(href):
                continue
            if _REGULAMENTO_KW_RE.search(a.get_text(strip=True)):
                return href if href.startswith("http") else urljoin(page_url, href)

        # Fallback: qualquer PDF
        for a in soup.find_all("a", href=_PDF_EXT_RE):
            href = a["href"]
            return href if href.startswith("http") else urljoin(page_url, href)

        return None

    def _fetch_pdf_sections(self, pdf_url: str) -> list[dict]:
        """Baixa PDF e extrai seções numeradas com pdfplumber."""
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber não instalado — PDFs ignorados (pip install pdfplumber)")
            return []

        try:
            resp = self._http.get(pdf_url, timeout=60, stream=True)
            resp.raise_for_status()

            chunks, total = [], 0
            for chunk in resp.iter_content(8192):
                chunks.append(chunk)
                total += len(chunk)
                if total > 15 * 1024 * 1024:  # limite 15 MB
                    break

            pdf_bytes = b"".join(chunks)
            pages_text = []
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages[: self.max_pdf_pages]:
                    pages_text.append(page.extract_text() or "")

            full_text = "\n".join(pages_text).strip()
            if not full_text:
                return []

            return self._sections_from_pdf_text(full_text)

        except Exception as e:
            logger.error(f"Erro ao extrair PDF {pdf_url}: {e}")
            return []

    def _sections_from_pdf_text(self, text: str) -> list[dict]:
        """Detecta seções numeradas (1. Objeto, 2.1 Elegibilidade...) no texto do PDF."""
        matches = list(_NUMBERED_SECTION_RE.finditer(text))

        if len(matches) >= 2:
            sections = []
            for i, match in enumerate(matches):
                start = match.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                content = text[start:end].strip()
                title = match.group(1).strip().rstrip(".")
                if len(content) > 30:
                    sections.append({"title": title, "content": content})
            if sections:
                return sections

        # Fallback: documento inteiro como seção única
        return [{"title": "Regulamento Completo", "content": text}]

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _save(self, edital_id: str, url: str, sections: list[dict]) -> None:
        """Persiste seções em section_index/{edital_id}.json."""
        SECTION_INDEX_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "edital_id": edital_id,
            "source": "live_fetch",
            "title": url,
            "sections": sections,
        }
        path = SECTION_INDEX_DIR / f"{edital_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
