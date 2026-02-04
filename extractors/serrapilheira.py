"""
Scraper para chamadas públicas do Instituto Serrapilheira.
https://serrapilheira.org/todas-as-chamadas/
"""
import re
from .base import BaseScraper


class SerrapilheiraScraper(BaseScraper):
    """Scraper para editais do Instituto Serrapilheira."""

    BASE_URL = "https://serrapilheira.org/todas-as-chamadas/"

    def __init__(self):
        super().__init__(source_name="SERRAPILHEIRA", output_subdir="serrapilheira_raw")

    def extract(self) -> list:
        """Extrai chamadas públicas do Serrapilheira."""
        print(f"Acessando: {self.BASE_URL}")

        soup = self._fetch_page(self.BASE_URL)

        chamadas = []
        seen_urls = set()

        # Busca todos os links para chamadas (padrão: /ano/*)
        links = soup.find_all('a', href=True)

        for link in links:
            href = link.get('href', '')

            # Filtra apenas links de chamadas
            if '/ano/' not in href or href in seen_urls:
                continue

            seen_urls.add(href)

            # Extrai título do link ou do atributo title
            titulo = link.get('title') or link.get_text().strip()
            if not titulo or len(titulo) < 5:
                continue

            # Limpa título
            titulo = self._clean_text(titulo)

            # Extrai ano do URL
            ano = self._extract_year(href)

            # Determina tipo/categoria
            categoria = self._categorize(href, titulo)

            # Determina status (chamadas recentes = possivelmente abertas)
            status = "HISTÓRICO"
            if ano and int(ano) >= 2025:
                status = "RECENTE"

            chamada = {
                "fonte": self.source_name,
                "titulo": titulo,
                "url": href,
                "ano": ano,
                "categoria": categoria,
                "status": status,
                "data_extracao": self._get_date()
            }

            chamadas.append(chamada)

        # Remove duplicatas por URL
        unique_chamadas = {c['url']: c for c in chamadas}.values()
        chamadas = list(unique_chamadas)

        print(f"Encontradas {len(chamadas)} chamadas.")

        if chamadas:
            self._save(chamadas, prefix="serrapilheira")

        return chamadas

    def _extract_year(self, url: str) -> str:
        """Extrai ano do URL da chamada."""
        match = re.search(r'(20\d{2})', url)
        return match.group(1) if match else None

    def _categorize(self, url: str, titulo: str) -> str:
        """Categoriza a chamada baseado no URL e título."""
        url_lower = url.lower()
        titulo_lower = titulo.lower()

        if 'ciencia' in url_lower or 'cientista' in titulo_lower:
            return "CIÊNCIA"
        elif 'jornalis' in url_lower or 'jornalis' in titulo_lower:
            return "JORNALISMO"
        elif 'podcast' in url_lower or 'camp' in url_lower:
            return "DIVULGAÇÃO"
        elif 'ecologia' in url_lower or 'ecologia' in titulo_lower:
            return "ECOLOGIA"
        elif 'formacao' in url_lower or 'formação' in titulo_lower:
            return "FORMAÇÃO"
        else:
            return "OUTROS"


# Permite execução direta
if __name__ == "__main__":
    scraper = SerrapilheiraScraper()
    scraper.run()
