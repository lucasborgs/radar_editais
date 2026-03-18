# Módulo de extratores de editais
# Registry centralizado: adicionar novas fontes aqui e o run_all.py detecta automaticamente.

from .base import BaseScraper
from .fapesp import FAPESPScraper
from .finep import FINEPScraper
from .bndes import BNDESScraper
from .embrapii import EMBRAPIIScraper

# =============================================================================
# SCRAPER REGISTRY
# Formato de cada entrada:
#   chave: identificador único
#   valor: dict com source_name, bronze_dir, silver_name, cls, kwargs
# =============================================================================

SCRAPER_REGISTRY = {
    # ── Editais tradicionais (event-driven, com prazo) ─────────────────────
    "FAPESP": dict(
        source_name="FAPESP", bronze_dir="fapesp_raw",
        silver_name="FAPESP", cls=FAPESPScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
    "FINEP": dict(
        source_name="FINEP", bronze_dir="finep_raw",
        silver_name="FINEP", cls=FINEPScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
    "BNDES": dict(
        source_name="BNDES", bronze_dir="bndes_raw",
        silver_name="BNDES", cls=BNDESScraper, kwargs={},
        historical_kwargs={"include_historical": True},
    ),
    # ── Fluxo contínuo — diretório de unidades executoras ─────────────────
    "EMBRAPII": dict(
        source_name="EMBRAPII", bronze_dir="embrapii_raw",
        silver_name="EMBRAPII", cls=EMBRAPIIScraper, kwargs={},
        # EMBRAPII não tem histórico — unidades são permanentes
    ),
}

__all__ = [
    "BaseScraper",
    "SCRAPER_REGISTRY",
    "FAPESPScraper", "FINEPScraper", "BNDESScraper", "EMBRAPIIScraper",
]
