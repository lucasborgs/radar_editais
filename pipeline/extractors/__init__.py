# Módulo de extratores de editais
# SCRAPER_REGISTRY contém apenas as fontes ativas na v1 (FINEP).
# INACTIVE_SCRAPERS preserva as demais fontes para implementação na v2.

from .base import BaseScraper
from .fapesp import FAPESPScraper
from .finep import FINEPScraper
from .bndes import BNDESScraper
from .embrapii import EMBRAPIIScraper

# =============================================================================
# FONTES ATIVAS — v1
# =============================================================================

SCRAPER_REGISTRY = {
    "FINEP": dict(
        source_name="FINEP", bronze_dir="finep_raw",
        cls=FINEPScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
}

# =============================================================================
# FONTES INATIVAS — v2
# =============================================================================

INACTIVE_SCRAPERS = {
    "FAPESP": dict(
        source_name="FAPESP", bronze_dir="fapesp_raw",
        cls=FAPESPScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
    "BNDES": dict(
        source_name="BNDES", bronze_dir="bndes_raw",
        cls=BNDESScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
    "EMBRAPII": dict(
        source_name="EMBRAPII", bronze_dir="embrapii_raw",
        cls=EMBRAPIIScraper, kwargs={},
    ),
}

__all__ = [
    "BaseScraper",
    "SCRAPER_REGISTRY",
    "INACTIVE_SCRAPERS",
    "FAPESPScraper", "FINEPScraper", "BNDESScraper", "EMBRAPIIScraper",
]
