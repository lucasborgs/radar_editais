# Módulo de extratores de editais.
# Para adicionar uma nova fonte: implemente um BaseScraper concreto e registre
# em SCRAPER_REGISTRY — `python scripts/run_all.py` percorre o registry.
#
# Chave do registry = `source` canônico (lowercase), igual à chave em
# WIKI.md §12.4 `source_adapters` e ao prefixo usado em `core/edital_id.py`.
# `display_name` é só pra logging/UX.

from .base import BaseScraper
from .fapesp import FAPESPScraper
from .finep import FINEPScraper

SCRAPER_REGISTRY = {
    "finep": dict(
        source="finep", display_name="FINEP", bronze_dir="finep_raw",
        cls=FINEPScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
    "fapesp": dict(
        source="fapesp", display_name="FAPESP", bronze_dir="fapesp_raw",
        cls=FAPESPScraper, kwargs={},
        # FAPESP não tem modo histórico via API; histórico é o que já está em
        # bronze_data/fapesp_raw/. Adapter L1 lê o snapshot mais recente.
    ),
}

__all__ = [
    "BaseScraper",
    "SCRAPER_REGISTRY",
    "FINEPScraper",
    "FAPESPScraper",
]
