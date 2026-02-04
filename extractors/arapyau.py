"""
Scraper para o Instituto Arapyaú.
https://arapyau.org.br/

Nota: O Arapyaú não trabalha com editais públicos tradicionais.
O foco é no Programa de Fellows que opera em ciclos.
Este scraper monitora a página do programa e as notícias
para identificar novas oportunidades.
"""
import re
from .base import BaseScraper


class ArapyauScraper(BaseScraper):
    """Scraper para o Instituto Arapyaú."""

    BASE_URL = "https://arapyau.org.br"
    FELLOWS_URL = "https://arapyau.org.br/programa-fellows/"
    API_POSTS = "https://arapyau.org.br/wp-json/wp/v2/posts"

    def __init__(self, buscar_noticias: bool = True):
        """
        Args:
            buscar_noticias: Se True, também busca notícias recentes
        """
        super().__init__(source_name="ARAPYAU", output_subdir="arapyau_raw")
        self.buscar_noticias = buscar_noticias

    def extract(self) -> list:
        """Extrai informações do programa de fellows e notícias."""
        all_items = []

        # Extrai info do programa de fellows
        fellows_info = self._extract_fellows_program()
        if fellows_info:
            all_items.append(fellows_info)

        # Busca notícias que podem conter oportunidades
        if self.buscar_noticias:
            noticias = self._extract_noticias()
            all_items.extend(noticias)

        print(f"Total: {len(all_items)} itens extraídos.")

        if all_items:
            self._save(all_items, prefix="arapyau")

        return all_items

    def _extract_fellows_program(self) -> dict:
        """Extrai informações do Programa de Fellows."""
        print(f"Acessando: {self.FELLOWS_URL}")

        soup = self._fetch_page(self.FELLOWS_URL)

        # Extrai descrição do programa
        descricao = None
        desc_elem = soup.find('p', string=re.compile(r'Programa de Fellows', re.IGNORECASE))
        if not desc_elem:
            # Tenta buscar no conteúdo geral
            for p in soup.find_all('p'):
                texto = p.get_text()
                if 'Fellows' in texto and 'fortalecer' in texto.lower():
                    descricao = self._clean_text(texto)
                    break

        # Busca informações sobre ciclos
        ciclos = []
        for elem in soup.find_all(['p', 'div']):
            texto = elem.get_text()
            if 'Ciclo' in texto and re.search(r'\d{4}', texto):
                ciclo_info = self._clean_text(texto)
                if ciclo_info and len(ciclo_info) > 5:
                    ciclos.append(ciclo_info)

        # Remove duplicatas de ciclos
        ciclos = list(dict.fromkeys(ciclos))

        return {
            "fonte": self.source_name,
            "tipo": "PROGRAMA",
            "titulo": "Programa de Fellows Arapyaú",
            "url": self.FELLOWS_URL,
            "descricao": descricao,
            "ciclos": ciclos[:5],  # Limita a 5 ciclos mais recentes
            "status": "ATIVO",
            "data_extracao": self._get_date()
        }

    def _extract_noticias(self) -> list:
        """Extrai notícias recentes via API WordPress."""
        print(f"Buscando notícias via API...")

        noticias = []
        keywords = ['fellow', 'edital', 'inscri', 'selecao', 'chamada', 'oportunidade']

        try:
            import requests
            response = requests.get(
                self.API_POSTS,
                params={'per_page': 20},
                headers=self.DEFAULT_HEADERS,
                timeout=15
            )

            if response.status_code == 200:
                posts = response.json()

                for post in posts:
                    # Extrai título (pode estar em formato diferente)
                    titulo_raw = post.get('title', {})
                    if isinstance(titulo_raw, dict):
                        titulo = titulo_raw.get('rendered', '')
                    else:
                        titulo = str(titulo_raw)

                    # Limpa título (pode ter HTML)
                    titulo = re.sub(r'<[^>]+>', '', titulo)
                    titulo = self._clean_text(titulo)

                    # Remove sufixo " - Arapyaú" do título
                    titulo = re.sub(r'\s*-\s*Arapyaú$', '', titulo)

                    link = post.get('link', '')
                    data = post.get('date', '')[:10]  # YYYY-MM-DD

                    # Filtra posts em inglês e duplicados
                    if '/en/' in link:
                        continue

                    # Verifica se é relevante (contém keywords)
                    titulo_lower = titulo.lower()
                    is_relevant = any(kw in titulo_lower for kw in keywords)

                    noticia = {
                        "fonte": self.source_name,
                        "tipo": "NOTICIA",
                        "titulo": titulo,
                        "url": link,
                        "data_publicacao": data,
                        "relevante": is_relevant,
                        "data_extracao": self._get_date()
                    }

                    noticias.append(noticia)

                print(f"  Encontradas {len(noticias)} notícias")

        except Exception as e:
            print(f"  Erro ao buscar notícias: {e}")

        return noticias


# Permite execução direta
if __name__ == "__main__":
    scraper = ArapyauScraper()
    scraper.run()
