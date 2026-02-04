# Módulo de extratores de editais
from .base import BaseScraper
from .finep import FINEPScraper
from .fapesp import FAPESPScraper
from .bndes import BNDESScraper
from .pncp import PNCPScraper
from .serrapilheira import SerrapilheiraScraper
from .itausocial import ItauSocialScraper
from .cnpq import CNPqScraper
from .arapyau import ArapyauScraper
from .lemann import LemannScraper

__all__ = [
    'BaseScraper',
    # Fontes originais
    'FINEPScraper',
    'FAPESPScraper',
    'BNDESScraper',
    'PNCPScraper',
    # Novas fontes
    'SerrapilheiraScraper',
    'ItauSocialScraper',
    'CNPqScraper',
    'ArapyauScraper',
    'LemannScraper',
]
