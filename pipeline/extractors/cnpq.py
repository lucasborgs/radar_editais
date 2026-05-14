"""
CNPq scraper — SCAFFOLDING (Fase 4 #27).

> **Status:** stub. Diferentemente de FAPESP/BNDES/EMBRAPII (que já têm
> scrapers implementados em INACTIVE_SCRAPERS), CNPq ainda não foi mapeado.
> A estrutura do site CNPq é distinta — usa um portal de chamadas próprio.

URLs de referência:
- http://memoria2.cnpq.br/web/guest/chamadas-publicas — listagem corrente
- http://resultado.cnpq.br/ — resultados (potencialmente outra base)
- Páginas podem usar paginação JS-based; pode exigir Playwright.

Convenção de output: bronze_data/cnpq_raw/<chamada_id>/edital.html + metadata.json
"""
from .base import BaseScraper


class CNPqScraper(BaseScraper):
    """Scraper para chamadas do CNPq (stub)."""

    BASE_URL = "http://memoria2.cnpq.br/web/guest/chamadas-publicas"

    def __init__(self):
        super().__init__(source_name="CNPq", output_subdir="cnpq_raw")

    def extract(self, include_historical: bool = False) -> list:
        """Extrai chamadas públicas do CNPq.

        TODO: implementar. Pontos de partida:
          1. GET BASE_URL — pode redirecionar para SSO. Investigar headers
             necessários ou se há endpoint público alternativo.
          2. Se a página principal usar JS-rendering para carregar a lista,
             considerar Playwright on-demand (já listado nas dependências do
             projeto como decisão arquitetural).
          3. Parsear título, número da chamada, data limite, link de detalhes.
          4. Para cada chamada, seguir o link e capturar o PDF do edital.
          5. Salvar em self.output_dir / <chamada_id> / e retornar metadata dicts.

        Quando implementar, adicionar a entry em SCRAPER_REGISTRY (ou
        INACTIVE_SCRAPERS para validar antes) em pipeline/extractors/__init__.py.
        """
        raise NotImplementedError(
            "CNPqScraper.extract ainda não implementado — ver TODO no header"
        )
