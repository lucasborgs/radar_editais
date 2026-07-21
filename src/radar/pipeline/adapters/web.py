"""
Web Source Adapter — L1 (WIKI.md §12, strategy=html_clean).

Fonte web genérica: o bronze (web_raw, escrito por pipeline/extractors/web.py)
guarda HTML CRU por URL. O adapter lê o bronze, acha o item pelo `native_id`
(= `url_hash`), re-limpa o HTML→texto via base.html_to_text (re-limpar a cada
run permite trocar o extrator sem re-fetch — §12 design da fonte web), e
devolve 1 entrada de Documento Canônico (§12.3) com o texto fatiado em units.

Estratégia §12.4: `html_clean`. Sem download de PDF, sem `pdfplumber`.
"""
from __future__ import annotations

import json
import logging

from radar.core.config import BRONZE_DIR

from .base import CanonicalDoc, SourceAdapter, coletado_em, html_to_text, split_into_units

logger = logging.getLogger(__name__)

_BRONZE_DIR = BRONZE_DIR / "web_raw"


def _load_web_bronze() -> list[dict]:
    """Une TODOS os arquivos bronze web, dedup por `url_hash` (mais novo vence).

    web é multi-feeder (duas torneiras no mesmo bronze: WebScraper da seed list
    manual + Descoberta da busca livre), cada uma gravando seus próprios
    arquivos. Diferente de FINEP/FAPESP (snapshot — só o último arquivo conta),
    aqui o bronze é ADITIVO: precisamos ver itens de ambas as torneiras. Os
    arquivos são lidos em ordem (antigo→novo), então um `url_hash` re-gravado
    por um scrape mais recente sobrescreve a versão antiga."""
    if not _BRONZE_DIR.exists():
        logger.warning("web adapter: bronze dir ausente: %s", _BRONZE_DIR)
        return []
    by_hash: dict[str, dict] = {}
    for path in sorted(_BRONZE_DIR.glob("*.json")):
        try:
            for it in json.loads(path.read_text(encoding="utf-8")):
                h = it.get("url_hash")
                if h:
                    by_hash[h] = it
        except Exception as e:
            logger.warning("web adapter: erro lendo %s: %s", path.name, e)
    return list(by_hash.values())


class Adapter(SourceAdapter):
    """L1 web — bronze JSON (HTML cru) → Documento Canônico (re-limpa por run)."""

    def to_documents(self, edital_id: str) -> CanonicalDoc:
        """Recebe o native_id (= `url_hash`, ex.: 'ab12cd34ef56') e retorna o
        conteúdo da página como Documento Canônico, fatiado em units."""
        bronze = _load_web_bronze()
        if not bronze:
            return []

        match: dict | None = next(
            (it for it in bronze if it.get("url_hash") == edital_id), None
        )
        if match is None:
            logger.info("web adapter: url_hash %s não encontrado no bronze", edital_id)
            return []

        # Preferimos re-limpar do HTML cru (re-derivável); caímos para o
        # snapshot `texto_cru` se o bronze não tiver `html` (item legado).
        html = match.get("html") or ""
        texto = html_to_text(html) if html else (match.get("texto_cru") or "")
        texto = texto.strip()
        if not texto:
            logger.info("web adapter: url_hash %s sem texto utilizável", edital_id)
            return []

        doc_name = match.get("title") or "pagina-web"
        return [{"doc_name": doc_name, "units": split_into_units(texto)}]

    def provenance(self, edital_id: str) -> dict:
        """URL da página (fonte da Descoberta promovida) + data de coleta, casando
        por `url_hash`. É a URL que a staging carrega para o build (D14/PR4)."""
        match = next(
            (it for it in _load_web_bronze() if it.get("url_hash") == edital_id), None
        )
        if match is None:
            return {}
        prov: dict = {"fonte": match.get("fonte") or "web"}
        if match.get("url"):
            prov["url"] = match["url"]
        if coletado_em(match):
            prov["coletado_em"] = coletado_em(match)
        return prov
