# Módulo de extratores de editais.
# Para adicionar uma nova fonte: implemente um BaseScraper concreto e registre
# em SCRAPER_REGISTRY — `python scripts/run_all.py` percorre o registry.

from .base import BaseScraper
from .finep import FINEPScraper

SCRAPER_REGISTRY = {
    "FINEP": dict(
        source_name="FINEP", bronze_dir="finep_raw",
        cls=FINEPScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
}

__all__ = [
    "BaseScraper",
    "SCRAPER_REGISTRY",
    "FINEPScraper",
]
