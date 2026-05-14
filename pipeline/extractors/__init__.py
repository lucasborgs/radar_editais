# Módulo de extratores de editais
# SCRAPER_REGISTRY contém apenas as fontes ativas na v1 (FINEP).
# INACTIVE_SCRAPERS preserva as demais fontes para implementação na v2.
# Para ativar uma fonte inativa: mover a entry de INACTIVE_SCRAPERS para
# SCRAPER_REGISTRY e rodar `python scripts/run_all.py` (Fase 4 #27).

from .base import BaseScraper
from .bndes import BNDESScraper
from .cnpq import CNPqScraper
from .embrapii import EMBRAPIIScraper
from .fapesp import FAPESPScraper
from .finep import FINEPScraper

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
# FONTES INATIVAS — v2 (scrapers implementados, aguardando ativação)
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

# =============================================================================
# FONTES STUB — implementação pendente (Fase 4 #27)
# =============================================================================
# CNPq existe como stub em cnpq.py mas sua lógica de scraping não foi mapeada.
# Não entra em SCRAPER_REGISTRY até que `extract()` seja implementado.
STUB_SCRAPERS = {
    "CNPq": dict(
        source_name="CNPq", bronze_dir="cnpq_raw",
        cls=CNPqScraper, kwargs={},
    ),
}

__all__ = [
    "BaseScraper",
    "SCRAPER_REGISTRY",
    "INACTIVE_SCRAPERS",
    "STUB_SCRAPERS",
    "FAPESPScraper", "FINEPScraper", "BNDESScraper", "EMBRAPIIScraper", "CNPqScraper",
]
