"""
Scraper FAPESP — listagem de chamadas públicas (HTML server-rendered).

Origem: https://fapesp.br/chamadas/ é Laravel server-rendered com accordion;
cada chamada é `<h3><a href="https://fapesp.br/{id}">titulo</a></h3>`. A página
individual `/{id}` retorna meta-refresh para `/{id}/{slug}` que tem o texto
normativo do edital inline (estratégia §12.4 `html_body`).

Spike validado em 2026-05-29 — ver memory: project_multi_fonte_fase1.

Output bronze: JSON com chamadas no contrato da fonte documentado em
`wikis/fapesp.md`, consumido pelo Source Adapter:
  titulo, url, data_limite (yyyy-mm-dd), status (ABERTA|FLUXO_CONTINUO),
  modalidades (string), areas (string), texto_cru, fluxo_continuo (bool),
  fonte, data_extracao.

Dedup intra-arquivo por URL normalizada. Encoding misto (listagem ISO-8859-1,
páginas individuais UTF-8) — detectado via Content-Type da resposta.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper

logger = logging.getLogger(__name__)


_LISTAGEM_URL = "https://fapesp.br/chamadas/"
_BASE = "https://fapesp.br"
_URL_ID_RE = re.compile(r"/(\d+)(?:/|$)")
_META_REFRESH_RE = re.compile(r'url=[\'"]?([^\'"\s>]+)', re.IGNORECASE)
_DATE_BR_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# Campos chave esperados na página individual da chamada FAPESP.
# Linhas começam com "Modalidade de Apoio:", "Prazo ...", "Áreas:", etc.
_MODALIDADE_RE = re.compile(r"Modalidade(?:s)?\s+(?:de\s+Apoio)?:?\s*([^\n\r]+)", re.IGNORECASE)
_AREAS_RE = re.compile(r"(?:Áreas?|Areas?)\s+(?:do\s+conhecimento)?\s*:\s*([^\n\r]+)", re.IGNORECASE)
_PRAZO_RE = re.compile(
    r"(?:Prazo\s+(?:final|para)|Data[s]?\s*-?limite|Data\s+limite)[^\n\r:]*:\s*(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)
_FLUXO_RE = re.compile(r"fluxo\s+cont[ií]nuo", re.IGNORECASE)


class FAPESPScraper(BaseScraper):
    """Scraper FAPESP — HTML listagem + página individual por chamada."""

    DOWNLOAD_THROTTLE = 0.3  # segundos entre fetches de página individual

    def __init__(self, max_chamadas: int | None = None):
        """`max_chamadas` limita a iteração (útil em smoke test). None = todas."""
        super().__init__(source_name="FAPESP", output_subdir="fapesp_raw")
        self.max_chamadas = max_chamadas

    # ------------------------------------------------------------------
    # LISTAGEM
    # ------------------------------------------------------------------

    def _fetch_decoded(self, url: str) -> str:
        """Fetch + detecta encoding via Content-Type (gotcha §8 wikis/fapesp.md)."""
        resp = requests.get(url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        # requests auto-detecta charset; força fallback razoável se vier vazio
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            # FAPESP listagem declara iso-8859-1; o conteúdo real é misto.
            # Resolver: ler como ISO e depois converter, ou aceitar mojibake leve.
            pass
        return resp.text

    def _follow_meta_refresh(self, html_text: str, fallback_url: str) -> str:
        """Detecta `<meta http-equiv="refresh" url="...">` e retorna URL alvo
        (absoluta). Se não houver meta-refresh, retorna `fallback_url` (o usado
        no fetch original)."""
        m = _META_REFRESH_RE.search(html_text)
        if not m:
            return fallback_url
        target = m.group(1).strip().strip("'\"")
        if target.startswith("http"):
            return target
        if target.startswith("/"):
            return _BASE + target
        return fallback_url

    def _enumerate_chamadas(self) -> list[tuple[str, str]]:
        """Retorna `[(url, titulo)]` da listagem `/chamadas/`. Dedup por URL."""
        html_text = self._fetch_decoded(_LISTAGEM_URL)
        soup = BeautifulSoup(html_text, "html.parser")

        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        # Cada chamada é `<h3><a href="https://fapesp.br/{id}">titulo</a></h3>`.
        for a in soup.select("h3 > a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            # Normaliza para HTTPS + descarta query/fragment
            href = href.replace("http://", "https://").split("#")[0].split("?")[0].rstrip("/")
            if _URL_ID_RE.search(href) is None:
                # Só chamadas com id numérico no path. Pula menus/navegação.
                continue
            if href in seen:
                continue
            seen.add(href)
            titulo = a.get_text(strip=True)
            items.append((href, titulo))
        return items

    # ------------------------------------------------------------------
    # PÁGINA INDIVIDUAL
    # ------------------------------------------------------------------

    def _fetch_chamada_page(self, url: str) -> str:
        """Fetch + segue meta-refresh + retorna HTML decoded (UTF-8 esperado)."""
        first = self._fetch_decoded(url)
        target = self._follow_meta_refresh(first, url)
        if target != url:
            return self._fetch_decoded(target)
        return first

    @staticmethod
    def _clean_text(raw: str) -> str:
        """Limpa HTML entities (duplo-escape FAPESP) e tags HTML para texto plano."""
        # Duplo-unescape: `&amp;ccedil;` → `&ccedil;` → `ç`
        unescaped = html.unescape(html.unescape(raw))
        soup = BeautifulSoup(unescaped, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        # Compacta linhas vazias
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _parse_chamada(self, url: str, titulo: str) -> dict | None:
        """Faz fetch + extrai campos canônicos. Retorna dict ou None em falha."""
        try:
            html_text = self._fetch_chamada_page(url)
        except requests.RequestException as e:
            logger.warning("FAPESP scraper: erro buscando %s: %s", url, e)
            return None

        texto_cru = self._clean_text(html_text)

        # Modalidade
        modalidade = None
        m_mod = _MODALIDADE_RE.search(texto_cru)
        if m_mod:
            modalidade = m_mod.group(1).strip(" .;,")

        # Área(s) — primeira ocorrência
        areas = None
        m_ar = _AREAS_RE.search(texto_cru)
        if m_ar:
            areas = m_ar.group(1).strip(" .;,")

        # Prazo (BR → ISO yyyy-mm-dd)
        data_limite = None
        m_pz = _PRAZO_RE.search(texto_cru)
        if m_pz:
            data_limite = self._br_to_iso(m_pz.group(1))

        fluxo_continuo = bool(_FLUXO_RE.search(texto_cru))
        status = "FLUXO_CONTINUO" if fluxo_continuo else ("ABERTA" if data_limite else "Desconhecido")

        return {
            "titulo": titulo,
            "url": url,
            "data_limite": data_limite,
            "status": status,
            "modalidades": modalidade,
            "areas": areas,
            "texto_cru": texto_cru,
            "fluxo_continuo": fluxo_continuo,
            "fonte": "FAPESP",
            "data_extracao": self._get_timestamp(),
        }

    @staticmethod
    def _br_to_iso(br_date: str) -> str | None:
        m = _DATE_BR_RE.match(br_date.strip())
        if not m:
            return None
        d, mo, y = m.groups()
        try:
            return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # EXTRACT (entry point)
    # ------------------------------------------------------------------

    def extract(self) -> list[dict]:
        chamadas_urls = self._enumerate_chamadas()
        if self.max_chamadas is not None:
            chamadas_urls = chamadas_urls[: self.max_chamadas]
        logger.info("FAPESP scraper: enumeradas %d chamadas", len(chamadas_urls))

        results: list[dict] = []
        for url, titulo in chamadas_urls:
            row = self._parse_chamada(url, titulo)
            if row:
                results.append(row)
            time.sleep(self.DOWNLOAD_THROTTLE)

        if results:
            self._save(results, prefix="fapesp_scan")
        return results
