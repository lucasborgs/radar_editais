"""
Scraper para editais do Itaú Social.
https://www.itausocial.org.br/editais/
"""
from .base import BaseScraper


class ItauSocialScraper(BaseScraper):
    """Scraper para editais do Itaú Social."""

    BASE_URL = "https://www.itausocial.org.br/editais/"

    def __init__(self):
        super().__init__(source_name="ITAU_SOCIAL", output_subdir="itausocial_raw")

    def extract(self) -> list:
        """Extrai editais do Itaú Social."""
        print(f"Acessando: {self.BASE_URL}")

        soup = self._fetch_page(self.BASE_URL)

        editais = []

        # Busca links com classe 'edital'
        edital_links = soup.find_all('a', class_='edital')

        if not edital_links:
            # Fallback: busca links que contenham /editais/ no href
            edital_links = soup.find_all('a', href=lambda h: h and '/editais/' in h and h != '/editais/')

        print(f"Encontrados {len(edital_links)} elementos de edital.")

        seen_urls = set()

        for link in edital_links:
            href = link.get('href', '')

            # Normaliza URL
            if href.startswith('/'):
                url = f"https://www.itausocial.org.br{href}"
            elif href.startswith('http'):
                url = href
            else:
                continue

            # Evita duplicatas
            if url in seen_urls or url == self.BASE_URL:
                continue
            seen_urls.add(url)

            # Extrai título do h4 ou do texto do link
            h4 = link.find('h4')
            titulo = h4.get_text().strip() if h4 else link.get_text().strip()

            # Extrai descrição do p
            p = link.find('p')
            descricao = self._clean_text(p.get_text()) if p else None

            if not titulo or len(titulo) < 3:
                continue

            edital = {
                "fonte": self.source_name,
                "titulo": self._clean_text(titulo),
                "url": url,
                "descricao": descricao,
                "status": "DISPONÍVEL",
                "data_extracao": self._get_date()
            }

            editais.append(edital)

        print(f"Extraídos {len(editais)} editais.")

        if editais:
            self._save(editais, prefix="itausocial")

        return editais


# Permite execução direta
if __name__ == "__main__":
    scraper = ItauSocialScraper()
    scraper.run()
