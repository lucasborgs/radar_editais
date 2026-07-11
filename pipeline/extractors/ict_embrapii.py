"""
Extractor EMBRAPII — unidades credenciadas (ICTs parceiras).

EMBRAPII não lança edital: suas unidades são ICTs que viabilizam candidaturas
exigindo parceria. Logo este extractor produz **nós ict** (WIKI.md §6.1.2), não
editais — e NÃO entra no SCRAPER_REGISTRY (que percorre o ETL de edital).

Fonte: embrapii.org.br é WordPress. As unidades são um custom post type exposto
pela WP REST API — caminho limpo e estável, sem scraping de HTML renderizado por
JS (a listagem /nossas-unidades/ filtra client-side, então o HTML estático traz
só ~10 das 90 unidades).

Endpoints:
  /wp-json/wp/v2/units?per_page=100   → 90 unidades (title, link, content, acf, taxonomias)
  /wp-json/wp/v2/action_lines         → linhas de ação (id → nome)
  /wp-json/wp/v2/tech_skills          → competências técnicas (id → nome)

Cada unidade referencia termos das duas taxonomias por id; resolvemos para nomes
e juntamos em `areas_raw` (expertise crua, alvo do normalizador fino→macro na
extração do hipergrado). Contato/endereço vêm do bloco ACF.

Output bronze (bronze_data/ict_raw/embrapii_*.json), shape consumido por
core/kg/gold.py (`_ingest_icts` → entities kind=ict):
  name, slug, source='embrapii', kind='embrapii_unit', url, about,
  institution_type, address, contact{responsavel,email,telefone,site},
  areas_raw[list[str]], data_extracao.
"""
from __future__ import annotations

import html
import logging

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper

logger = logging.getLogger(__name__)

_API = "https://embrapii.org.br/wp-json/wp/v2"
_TAXONOMIES = ("action_lines", "tech_skills")


class EmbrapiiScraper(BaseScraper):
    """Coleta as unidades EMBRAPII via WP REST API e as materializa como ICTs."""

    def __init__(self):
        super().__init__(source_name="EMBRAPII", output_subdir="ict_raw")

    def _get_json(self, path: str, params: dict | None = None) -> list[dict]:
        """GET em endpoint da REST API, retorna lista de dicts. Levanta em erro
        HTTP — `run()` da base captura e degrada para []."""
        resp = requests.get(f"{_API}/{path}", headers=self.headers,
                             params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _load_taxonomy(self, name: str) -> dict[int, str]:
        """Mapa id → nome de uma taxonomia (paginando até esgotar)."""
        terms: dict[int, str] = {}
        page = 1
        while True:
            batch = self._get_json(name, {"per_page": 100, "page": page})
            if not batch:
                break
            for t in batch:
                terms[t["id"]] = html.unescape(t.get("name", "")).strip()
            if len(batch) < 100:
                break
            page += 1
        return terms

    @staticmethod
    def _plain_text(rendered_html: str) -> str:
        """Extrai texto limpo de um campo `*.rendered` do WP."""
        if not rendered_html:
            return ""
        text = BeautifulSoup(rendered_html, "html.parser").get_text(" ", strip=True)
        return " ".join(text.split())

    def extract(self) -> list[dict]:
        # 1) Resolve taxonomias (id → nome) uma vez.
        tax: dict[str, dict[int, str]] = {}
        for name in _TAXONOMIES:
            tax[name] = self._load_taxonomy(name)
            logger.info("EMBRAPII taxonomy %s: %d termos", name, len(tax[name]))

        # 2) Unidades (uma página cobre as ~90; paginamos por segurança).
        units: list[dict] = []
        page = 1
        while True:
            batch = self._get_json("units", {"per_page": 100, "page": page})
            if not batch:
                break
            units.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        logger.info("EMBRAPII units: %d", len(units))

        records: list[dict] = []
        for u in units:
            acf = u.get("acf") or {}

            # areas_raw = nomes dos termos das duas taxonomias referenciados.
            areas: list[str] = []
            for name in _TAXONOMIES:
                for term_id in (u.get(name) or []):
                    label = tax[name].get(term_id)
                    if label and label not in areas:
                        areas.append(label)

            address = ", ".join(
                p for p in (acf.get("address"), acf.get("city"), acf.get("uf_state"))
                if p
            )
            contact = {
                "responsavel": acf.get("contact_name") or "",
                "email": acf.get("contact_email") or "",
                "telefone": acf.get("contact_phone") or "",
                "site": acf.get("website") or "",
            }

            records.append({
                "name": html.unescape(u.get("title", {}).get("rendered", "")).strip(),
                "slug": u.get("slug", ""),
                "source": "embrapii",
                "kind": "embrapii_unit",
                "url": u.get("link", ""),
                "about": self._plain_text(u.get("content", {}).get("rendered", "")),
                "institution_type": acf.get("institution_type") or "",
                "address": address,
                "contact": contact,
                "areas_raw": areas,
                "data_extracao": self._get_date(),
            })

        return records


def run() -> list[dict]:
    """Roda o extractor e salva o bronze. Retorna os registros."""
    scraper = EmbrapiiScraper()
    records = scraper.run()
    if records:
        scraper._save(records, prefix="embrapii")
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = run()
    print(f"\nEMBRAPII: {len(out)} unidades extraídas.")
    if out:
        ex = out[0]
        print(f"  exemplo: {ex['name']}")
        print(f"    areas_raw ({len(ex['areas_raw'])}): {ex['areas_raw'][:5]}...")
