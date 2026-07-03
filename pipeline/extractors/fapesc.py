"""Scraper FAPESC — chamadas públicas (WordPress, HTML server-rendered).

Origem: https://fapesc.sc.gov.br/chamadas-abertas/ lista as chamadas ABERTAS
(WordPress). Cada chamada é um post com URL própria por slug
(`/edital-de-chamada-publica-fapesc-n-o-37-2026-.../`).

**FAPESC ≠ FAPESP (achado do piloto 2026-06-18).** O post WordPress só traz um
RESUMO (~600 chars, montado pelo Elementor); o texto normativo do edital vive
num **PDF anexo** linkado na própria página (âncora "ACESSE O EDITAL COMPLETO"),
sob `/wp-content/uploads/AAAA/MM/`. Por isso a estratégia §12.4 é `pdf` (como a
FINEP), não `html_body`: o scraper baixa o PDF do edital e extrai o texto
inline (pdfplumber), gravando-o em `texto_cru`. O adapter (que fatia `texto_cru`
em units) não muda — só a origem do texto muda.

Output bronze (shape do bronze_mapping em wikis/fapesc.md):
  native_id, titulo, url, data_limite (dd/mm/yyyy|None), status (ABERTA|FLUXO_CONTINUO),
  modalidades, areas, texto_cru, edital_pdf_url, content_source (pdf|html),
  fluxo_continuo (bool), fonte, data_extracao.

`native_id` = número-ano do edital extraído da URL (ex.: 37-2026). É a identidade
`fapesc:<native_id>`. Dedup intra-arquivo por URL normalizada.
"""
from __future__ import annotations

import io
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper

logger = logging.getLogger(__name__)

_LISTAGEM_URL = "https://fapesc.sc.gov.br/chamadas-abertas/"
_HOST = "fapesc.sc.gov.br"
# Links de edital (página individual): sob o domínio FAPESC, slug começando por "edital".
_EDITAL_HREF_RE = re.compile(r"https?://(?:www\.)?fapesc\.sc\.gov\.br/edital[^\"'#?]*", re.IGNORECASE)
# native_id: ".../...-n-o-37-2026-..." ou ".../...-no-54-2024-..." → (num, ano).
_NATIVE_RE = re.compile(r"n-?o-0*(\d+)-(\d{4})", re.IGNORECASE)
_DATE_BR_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
# Data de prazo: pega a data num CONTEXTO de submissão/encerramento (best-effort).
_PRAZO_CTX_RE = re.compile(
    r"(?:subm|prazo|encerr|inscri[çc]|at[ée]\s+(?:o\s+dia\s+)?|limite)[^\n\r]{0,80}?(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)
_FLUXO_RE = re.compile(r"fluxo\s+cont[ií]nuo", re.IGNORECASE)
# Âncora que aponta o PDF do edital ("ACESSE O EDITAL COMPLETO"). A palavra
# "edital" no texto do link é o sinal forte e estável (o NOME do arquivo varia:
# CP-04_2026.pdf, Edital-FAPESC-016_2026.pdf, Processo-FAPESC-...pdf).
_EDITAL_ANCHOR_RE = re.compile(r"\bedital\b", re.IGNORECASE)
# Recência de um anexo pelo path /wp-content/uploads/AAAA/MM/.
_UPLOADS_DATE_RE = re.compile(r"/wp-content/uploads/(\d{4})/(\d{2})/", re.IGNORECASE)

# Fallback defensivo se o doc (wikis/fapesc.md §skip_keywords) estiver ausente.
# Boilerplate institucional + peças de andamento que NÃO são o edital normativo.
_SKIP_KEYWORDS_FALLBACK = [
    "plano-de-integridade", "codigo_conduta", "resultado", "manual",
    "passo-a-passo", "retificacao", "errata", "cadastr", "sigfapesc",
]


def _skip_keywords() -> list[str]:
    """Skip-list autoritativa do doc (wikis/fapesc.md); fallback à constante."""
    from core.kg import schema  # import tardio: evita ciclo no import do pipeline
    return schema.skip_keywords("fapesc") or _SKIP_KEYWORDS_FALLBACK


class FAPESCScraper(BaseScraper):
    """Scraper FAPESC — listagem WordPress + edital via PDF anexo por chamada."""

    DOWNLOAD_THROTTLE = 0.3  # segundos entre fetches de página individual

    def __init__(self, max_chamadas: int | None = None):
        """`max_chamadas` limita a iteração (smoke test). None = todas."""
        super().__init__(source_name="FAPESC", output_subdir="fapesc_raw")
        self.max_chamadas = max_chamadas

    # ------------------------------------------------------------------
    # HTTP (isolado para mock nos testes)
    # ------------------------------------------------------------------

    def _fetch(self, url: str) -> str:
        """GET de uma página, decodificada. Levanta em erro de rede."""
        resp = requests.get(url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    def _extract_pdf_text(self, url: str) -> str:
        """Baixa o PDF em memória e extrai o texto (pdfplumber, per-página).
        Retorna "" em qualquer falha — o caller decide o fallback."""
        try:
            resp = requests.get(url, headers=self.headers, timeout=60)
            resp.raise_for_status()
            import pdfplumber
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                pages = [(p.extract_text() or "") for p in pdf.pages]
            return re.sub(r"\n{3,}", "\n\n", "\n\n".join(pages)).strip()
        except Exception as e:  # rede, PDF corrompido, scan sem texto, etc.
            logger.warning("FAPESC scraper: falha extraindo PDF %s: %s", url, e)
            return ""

    # ------------------------------------------------------------------
    # LISTAGEM
    # ------------------------------------------------------------------

    def _enumerate_chamadas(self) -> list[tuple[str, str]]:
        """`[(url, titulo)]` da listagem `/chamadas-abertas/`. Dedup por URL,
        preferindo o texto de âncora mais longo (o título, não 'leia mais')."""
        soup = BeautifulSoup(self._fetch(_LISTAGEM_URL), "html.parser")

        best: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not _EDITAL_HREF_RE.fullmatch(href.split("#")[0].split("?")[0]):
                continue
            url = href.split("#")[0].split("?")[0].replace("http://", "https://").rstrip("/")
            titulo = a.get_text(" ", strip=True)
            # mantém o título mais informativo (mais longo) para a mesma URL
            if url not in best or len(titulo) > len(best[url]):
                best[url] = titulo
        return list(best.items())

    # ------------------------------------------------------------------
    # PÁGINA INDIVIDUAL
    # ------------------------------------------------------------------

    @staticmethod
    def _native_id(url: str) -> str | None:
        m = _NATIVE_RE.search(url)
        return f"{int(m.group(1))}-{m.group(2)}" if m else None

    @staticmethod
    def _clean(html: str) -> str:
        """HTML do post → texto principal limpo (reusa o extractor canônico L1).
        Só usado como FALLBACK quando o PDF do edital não está disponível."""
        try:
            from pipeline.adapters.base import html_to_text  # noqa: PLC0415
            return html_to_text(html)
        except Exception:  # fallback defensivo
            soup = BeautifulSoup(html, "html.parser")
            for t in soup(["script", "style", "noscript", "nav", "header", "footer"]):
                t.decompose()
            return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()

    @staticmethod
    def _is_boilerplate(pdf_url: str, skip: list[str]) -> bool:
        """True se o nome do arquivo bate a skip-list (anexo não-normativo)."""
        fname = pdf_url.rstrip("/").rsplit("/", 1)[-1].lower()
        return any(kw in fname for kw in skip)

    @staticmethod
    def _uploads_recency(pdf_url: str) -> int:
        """Recência monotônica pelo path /uploads/AAAA/MM/ (0 se ausente)."""
        m = _UPLOADS_DATE_RE.search(pdf_url)
        return int(m.group(1)) * 100 + int(m.group(2)) if m else 0

    def _find_edital_pdf(self, soup: BeautifulSoup) -> str | None:
        """URL do PDF do edital. PRIMARY: âncora cujo texto cita "edital"
        (preferindo "completo"); FALLBACK: PDF não-boilerplate mais recente
        sob /wp-content/uploads/. None se a página não anexa PDF utilizável."""
        skip = _skip_keywords()
        pdfs: list[tuple[str, str]] = []  # (url_normalizada, anchor_text)
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if ".pdf" not in href.lower():
                continue
            url = href.split("#")[0].split("?")[0].replace("http://", "https://")
            pdfs.append((url, a.get_text(" ", strip=True)))
        if not pdfs:
            return None

        # PRIMARY: âncora menciona "edital" e não é boilerplate. Empate → prefere
        # a que diz "completo" (a consolidada), senão a mais recente.
        edital_anchors = [
            (url, anchor) for url, anchor in pdfs
            if _EDITAL_ANCHOR_RE.search(anchor) and not self._is_boilerplate(url, skip)
        ]
        if edital_anchors:
            completos = [ua for ua in edital_anchors if "completo" in ua[1].lower()]
            pool = completos or edital_anchors
            pool.sort(key=lambda ua: self._uploads_recency(ua[0]), reverse=True)
            return pool[0][0]

        # FALLBACK: PDF não-boilerplate mais recente.
        cand = [url for url, _ in pdfs if not self._is_boilerplate(url, skip)]
        if not cand:
            return None
        cand.sort(key=self._uploads_recency, reverse=True)
        return cand[0]

    def _parse_chamada(self, url: str, titulo: str) -> dict | None:
        try:
            html = self._fetch(url)
        except requests.RequestException as e:
            logger.warning("FAPESC scraper: erro buscando %s: %s", url, e)
            return None

        soup = BeautifulSoup(html, "html.parser")
        edital_pdf_url = self._find_edital_pdf(soup)

        # texto_cru = texto do PDF do edital; fallback ao resumo HTML se o PDF
        # não existir ou não extrair (não perde a chamada — só degrada).
        texto_cru = ""
        content_source = "html"
        if edital_pdf_url:
            texto_cru = self._extract_pdf_text(edital_pdf_url)
            if texto_cru:
                content_source = "pdf"
        if not texto_cru:
            texto_cru = self._clean(html)

        fluxo_continuo = bool(_FLUXO_RE.search(texto_cru))

        # Prazo: maior data em contexto de submissão/encerramento (rerratificações
        # empurram prazos pra frente → a mais distante é a vigente). None se nenhuma.
        # Limita a uma JANELA de ano plausível: o texto do PDF tem ruído de futuro
        # distante — o rodapé de assinatura SGP-e diz "válido até 15/04/2126" (100
        # anos), que casaria o contexto "até …" e venceria o max. Deadlines reais
        # ficam em [ano-1, ano+3].
        from datetime import date, datetime
        _yr_min, _yr_max = date.today().year - 1, date.today().year + 3
        data_limite = None
        parsed: list[tuple[str, str, str, str]] = []
        for d in _PRAZO_CTX_RE.findall(texto_cru):   # cada `d` = "dd/mm/yyyy"
            mm = _DATE_BR_RE.search(d)
            if mm:
                dd, mo, yy = mm.groups()
                if not (_yr_min <= int(yy) <= _yr_max):
                    continue  # descarta ruído de futuro distante (assinatura, etc.)
                parsed.append((f"{yy}{mo}{dd}", dd, mo, yy))  # chave ISO p/ ordenar
        if parsed:
            parsed.sort()
            _, dd, mo, yy = parsed[-1]
            try:
                data_limite = datetime(int(yy), int(mo), int(dd)).strftime("%d/%m/%Y")
            except ValueError:
                data_limite = None

        status = "FLUXO_CONTINUO" if fluxo_continuo else "ABERTA"  # origem = chamadas-ABERTAS
        return {
            "native_id": self._native_id(url) or url.rstrip("/").rsplit("/", 1)[-1][:60],
            "titulo": titulo,
            "url": url,
            "data_limite": data_limite,
            "status": status,
            "modalidades": None,   # FAPESC não estrutura modalidade no HTML
            "areas": None,         # idem áreas
            "texto_cru": texto_cru,
            "edital_pdf_url": edital_pdf_url,
            "content_source": content_source,
            "fluxo_continuo": fluxo_continuo,
            "fonte": "FAPESC",
            "data_extracao": self._get_timestamp(),
        }

    # ------------------------------------------------------------------
    # EXTRACT (entry point)
    # ------------------------------------------------------------------

    def extract(self) -> list[dict]:
        chamadas = self._enumerate_chamadas()
        if self.max_chamadas is not None:
            chamadas = chamadas[: self.max_chamadas]
        logger.info("FAPESC scraper: enumeradas %d chamadas abertas", len(chamadas))

        results: list[dict] = []
        for url, titulo in chamadas:
            row = self._parse_chamada(url, titulo)
            if row and row.get("texto_cru"):
                results.append(row)
            time.sleep(self.DOWNLOAD_THROTTLE)

        if results:
            self._save(results, prefix="fapesc_scan")
        return results
