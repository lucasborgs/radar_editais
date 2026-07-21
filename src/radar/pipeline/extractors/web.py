"""
WebScraper — fonte web genérica (L0, docs/domain/schema.md §12.4 strategy=html_clean).

Diferente de FINEP/FAPESP (que têm listagem estruturada própria), a fonte web
não descobre editais: ela indexa uma seed list CURADA de URLs, mantida na
tabela operacional `web_sources` (migration 018). Cada URL `active` vira um
edital `web:<url_hash>` (1 URL = 1 edital).

O scraper busca cada URL e grava **HTML cru** em bronze_data/web_raw/ (não
texto limpo). A limpeza HTML→texto é trabalho de L1 (pipeline/adapters/web.py
via base.html_to_text): guardar o HTML cru permite re-limpar com extrator
melhor SEM re-fetch (reprodutibilidade — decisão de design da fonte web).

Também grava um `texto_cru` (snapshot limpo) por item — NÃO é a fonte do
chunking (o adapter re-limpa do `html`), mas alimenta de graça o dedup de
bronze (BaseScraper._save), o filtro PME e a heurística de ICT (§5.10), que
leem o corpo textual do item bronze.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from radar.core.web_identity import normalize_web_url, web_url_hash
from radar.pipeline.adapters.base import html_to_text

from .base import BaseScraper

logger = logging.getLogger(__name__)


def _extract_title(soup: BeautifulSoup, fallback_url: str) -> str:
    """Título legível da página: og:title > <title> > slug do path do URL."""
    og = soup.find("meta", property="og:title")
    if og and og.get("content", "").strip():
        return og["content"].strip()
    if soup.title and soup.title.string and soup.title.string.strip():
        return re.sub(r"\s+", " ", soup.title.string).strip()
    slug = urlsplit(fallback_url).path.rstrip("/").split("/")[-1]
    return slug or fallback_url


class WebScraper(BaseScraper):
    """Fonte web genérica — seed list em `web_sources` → bronze HTML cru."""

    DOWNLOAD_THROTTLE = 0.5  # segundos entre fetches de URLs distintas

    def __init__(self, max_urls: int | None = None):
        """`max_urls` limita a iteração (útil em smoke test). None = todas."""
        super().__init__(source_name="Web", output_subdir="web_raw")
        self.max_urls = max_urls

    def _load_seed_urls(self) -> list[dict]:
        """Lê as URLs `active` de `web_sources` via service-role. [] se a tabela
        estiver vazia ou o DB indisponível (degrada como qualquer scraper)."""
        try:
            from radar.core.infra.db import get_supabase_service

            db = get_supabase_service()
            res = (
                db.table("web_sources")
                .select("url, label")
                .eq("active", True)
                .execute()
            )
            return res.data or []
        except Exception as e:
            logger.warning("WebScraper: falha ao ler web_sources (%s) — seed vazia", e)
            return []

    def _fetch_one(self, url: str, label: str | None) -> dict | None:
        """Busca uma URL e monta o item bronze. None em falha de fetch."""
        try:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
            html = resp.text
        except requests.RequestException as e:
            logger.warning("WebScraper: erro buscando %s: %s", url, e)
            return None

        soup = BeautifulSoup(html, "html.parser")
        title = label or _extract_title(soup, url)
        texto_cru = html_to_text(html)
        if not texto_cru.strip():
            logger.info("WebScraper: %s sem texto extraível — pulando", url)
            return None

        return {
            "url": normalize_web_url(url),
            "url_hash": web_url_hash(url),
            "title": title,
            "html": html,             # cru — o adapter L1 re-limpa daqui
            "texto_cru": texto_cru,   # snapshot p/ dedup/filtro/ICT
            "status": "Desconhecido",  # web não tem status estruturado; síntese infere
            "http_status": resp.status_code,
            # Curadoria humana (seed list) → confiável. A OUTRA torneira do
            # mesmo bronze (Descoberta) grava "provisorio". `_build_editais`
            # lê este campo por-item (§5.11).
            "verificacao": "verificado",
            "fonte": "Web",
            "data_extracao": self._get_timestamp(),
        }

    def extract(self) -> list[dict]:
        seeds = self._load_seed_urls()
        if self.max_urls is not None:
            seeds = seeds[: self.max_urls]
        logger.info("WebScraper: %d URLs na seed list (web_sources)", len(seeds))

        results: list[dict] = []
        seen_hashes: set[str] = set()
        for seed in seeds:
            url = (seed.get("url") or "").strip()
            if not url:
                continue
            h = web_url_hash(url)
            if h in seen_hashes:  # dedup intra-run (URLs que normalizam igual)
                continue
            seen_hashes.add(h)
            row = self._fetch_one(url, seed.get("label"))
            if row:
                results.append(row)
            time.sleep(self.DOWNLOAD_THROTTLE)

        if results:
            self._save(results, prefix="web_scan")
        return results
