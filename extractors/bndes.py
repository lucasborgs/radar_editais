"""
Scraper para programas do BNDES.
Suporta múltiplos programas configuráveis (inovação, meio ambiente, social, etc).
"""
from .base import BaseScraper


# =============================================================================
# CONFIGURAÇÃO DE PROGRAMAS BNDES
# Para ativar/desativar um programa, altere "ativo" para True/False
# Para adicionar novo programa, copie um bloco e ajuste os parâmetros
# =============================================================================
PROGRAMAS_BNDES = {
    "inovacao": {
        "nome": "BNDES_INOVACAO",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/inovacao",
        "keywords": ["chamada", "edital", "seleção", "garagem", "funtec", "credenciamento"],
        "ativo": True
    },
    "meio_ambiente": {
        "nome": "BNDES_MEIO_AMBIENTE",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/meio-ambiente",
        "keywords": ["chamada", "edital", "clima", "sustentabilidade", "ambiental"],
        "ativo": False
    },
    "social": {
        "nome": "BNDES_SOCIAL",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/social",
        "keywords": ["chamada", "edital", "seleção", "social"],
        "ativo": False
    },
    "agropecuaria": {
        "nome": "BNDES_AGROPECUARIA",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/agropecuaria",
        "keywords": ["chamada", "edital", "rural", "agro"],
        "ativo": False
    },
}


class BNDESScraper(BaseScraper):
    """Scraper para programas do BNDES com suporte a múltiplas áreas."""

    def __init__(self):
        super().__init__(source_name="BNDES", output_subdir="bndes_raw")

    def extract(self) -> list:
        """Extrai oportunidades de todos os programas BNDES ativos."""
        programas_ativos = self._get_programas_ativos()

        if not programas_ativos:
            print("Nenhum programa ativo configurado em PROGRAMAS_BNDES.")
            return []

        print(f"Programas ativos: {', '.join(programas_ativos.keys())}")

        all_opportunities = []

        for programa_id, config in programas_ativos.items():
            opportunities = self._scrape_programa(programa_id, config)
            all_opportunities.extend(opportunities)

        if all_opportunities:
            self._save(all_opportunities, prefix="bndes_scan")

        return all_opportunities

    def _get_programas_ativos(self) -> dict:
        """Retorna apenas os programas com ativo=True."""
        return {k: v for k, v in PROGRAMAS_BNDES.items() if v.get("ativo", False)}

    def _scrape_programa(self, programa_id: str, config: dict) -> list:
        """Faz scraping de um programa específico."""
        opportunities = []
        processed_urls = set()

        print(f"\n  [{programa_id.upper()}] Acessando: {config['url']}")

        try:
            soup = self._fetch_page(config['url'], timeout=30)
            links = soup.find_all('a', href=True)
            print(f"  [{programa_id.upper()}] Analisando {len(links)} links...")

            for link in links:
                item = self._process_link(link, config, programa_id, processed_urls)
                if item:
                    opportunities.append(item)
                    print(f"    [ACHOU] {item['status']}: {item['titulo'][:50]}...")

        except Exception as e:
            print(f"  [{programa_id.upper()}] Erro: {e}")

        return opportunities

    def _process_link(self, link, config: dict, programa_id: str, processed_urls: set) -> dict:
        """Processa um link e retorna item se for relevante."""
        href = link.get('href', '')
        text = self._clean_text(link.get_text())

        if not text or len(text) < 5:
            return None

        # Normaliza URL
        full_url = self._normalize_url(href, config['url'])
        if not full_url or full_url in processed_urls:
            return None

        # Verifica keywords
        text_lower = text.lower()
        url_lower = full_url.lower()

        if not any(k in text_lower or k in url_lower for k in config['keywords']):
            return None

        processed_urls.add(full_url)

        # Captura contexto
        parent = link.find_parent(['p', 'div', 'li', 'td'])
        contexto = self._clean_text(parent.get_text()) if parent else text

        # Determina status
        status = self._determine_status(contexto)

        return {
            "fonte": config['nome'],
            "programa": programa_id,
            "titulo": text,
            "url": full_url,
            "contexto_capturado": contexto[:300],
            "status": status,
            "data_extracao": self._get_date()
        }

    def _normalize_url(self, href: str, base_url: str) -> str:
        """Normaliza URL relativa para absoluta."""
        if href.startswith('/'):
            return f"https://www.bndes.gov.br{href}"
        elif href.startswith('?'):
            return f"{base_url}{href}"
        elif href.startswith('http'):
            return href
        return None

    def _determine_status(self, contexto: str) -> str:
        """Determina status baseado no contexto."""
        contexto_lower = contexto.lower()
        if "aberta" in contexto_lower or "abertas" in contexto_lower or "inscrições" in contexto_lower:
            return "ABERTA"
        elif "encerrad" in contexto_lower or "finalizad" in contexto_lower:
            return "ENCERRADA"
        return "Em Análise"


# Permite execução direta do arquivo
if __name__ == "__main__":
    scraper = BNDESScraper()
    scraper.run()
