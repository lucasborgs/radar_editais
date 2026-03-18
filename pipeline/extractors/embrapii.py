"""
Scraper EMBRAPII — Diretório de Unidades Credenciadas
Fonte: https://embrapii.org.br/nossas-unidades/

Raspa o diretório completo de Unidades Embrapii com suas áreas de competência.
Diferente dos scrapers de editais: não há prazos — cada unidade representa
um mecanismo de fluxo contínuo sempre disponível.

Estratégia (tentadas em ordem):
  1. Export XLS: /nossas-unidades/?export_xls=1  (todos os dados de uma vez)
  2. Fallback: scraping paginado da listagem HTML  (até MAX_PAGES páginas)

Cada unidade retorna:
  fonte, titulo, unidade_nome, instituicao_base, uf, url,
  descricao, competencias, status="FLUXO_CONTINUO", prazo=None
"""
import re
import time
from datetime import datetime
from .base import BaseScraper

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


class EMBRAPIIScraper(BaseScraper):
    BASE_URL     = "https://embrapii.org.br/nossas-unidades/"
    EXPORT_URL   = "https://embrapii.org.br/nossas-unidades/?export_xls=1"
    BASE_SITE    = "https://embrapii.org.br"
    MAX_PAGES    = 15   # safety cap para paginação

    def __init__(self):
        super().__init__(source_name="EMBRAPII", output_subdir="embrapii_raw")

    # ------------------------------------------------------------------
    # Ponto de entrada
    # ------------------------------------------------------------------

    def extract(self) -> list:
        print(f"[EMBRAPII] Iniciando extração do diretório de unidades")

        results = self._try_xls_export()
        if results:
            print(f"  → {len(results)} unidades via export XLS, buscando descrições individuais...")
            results = self._enrich_descriptions(results)
        else:
            print("  XLS não disponível, usando scraping paginado...")
            results = self._scrape_paginated()
            print(f"  → {len(results)} unidades via scraping HTML")

        if results:
            self._save(results, prefix="embrapii_units")
        return results

    def _enrich_descriptions(self, units: list) -> list:
        """Visita a página individual de cada unidade para capturar a descrição completa."""
        enriched = []
        for i, unit in enumerate(units, 1):
            url = unit.get("url", "")
            nome = unit.get("unidade_nome", unit.get("titulo", ""))
            if not url:
                enriched.append(unit)
                continue
            print(f"  [{i}/{len(units)}] {nome}")
            detail = self._fetch_unit_detail(url, nome)
            if detail:
                unit = unit.copy()
                unit["descricao"] = detail["descricao"]
                if detail["competencias"]:
                    unit["competencias"] = detail["competencias"]
                if detail["instituicao_base"]:
                    unit["instituicao_base"] = detail["instituicao_base"]
                if detail["uf"]:
                    unit["uf"] = detail["uf"]
            enriched.append(unit)
            time.sleep(0.3)
        return enriched

    # ------------------------------------------------------------------
    # Estratégia 1: Export XLS
    # ------------------------------------------------------------------

    def _try_xls_export(self) -> list:
        if not HAS_OPENPYXL:
            print("  openpyxl não instalado; pulando export XLS (pip install openpyxl)")
            return []
        try:
            import io
            resp = self._fetch_raw(self.EXPORT_URL, timeout=60)
            if resp is None:
                return []
            content_type = resp.headers.get("Content-Type", "")
            if "spreadsheet" not in content_type and "excel" not in content_type and not resp.content[:4] in (b'PK\x03\x04',):
                # Não é um arquivo XLS/XLSX válido
                return []
            wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return []
            headers = [str(h).strip().lower() if h else "" for h in rows[0]]
            results = []
            for row in rows[1:]:
                item = self._parse_xls_row(headers, row)
                if item:
                    results.append(item)
            wb.close()
            return results
        except Exception as e:
            print(f"  Falha no XLS: {e}")
            return []

    def _parse_xls_row(self, headers: list, row: tuple) -> dict | None:
        """Mapeia linha do XLS para o schema de unidade Embrapii."""
        def get(keywords: list) -> str:
            for kw in keywords:
                for i, h in enumerate(headers):
                    if kw in h and i < len(row) and row[i]:
                        return str(row[i]).strip()
            return ""

        nome = get(["nome", "unidade", "name"])
        if not nome:
            return None

        competencias_raw = get(["competência", "competencia", "tecnol"])
        linhas_raw = get(["linha", "atuação", "atuacao"])
        instituicao = get(["instituição", "instituicao", "institution", "tipo"])
        cidade = get(["cidade", "city", "municipio"])
        uf = get(["estado", "uf", "state"])
        url_raw = get(["link", "url", "site"])
        if not url_raw:
            slug = re.sub(r'[^a-z0-9]+', '-', nome.lower()).strip('-')
            url_raw = f"{self.BASE_SITE}/unidades/{slug}/"

        competencias = self._split_competencias(competencias_raw)
        if linhas_raw:
            competencias += self._split_competencias(linhas_raw)
        competencias = list(dict.fromkeys(competencias))  # dedup mantendo ordem

        location = uf.upper()[:2] if uf else ""
        descricao_parts = []
        if instituicao:
            descricao_parts.append(instituicao)
        if cidade and uf:
            descricao_parts.append(f"{cidade}/{uf.upper()}")
        if competencias_raw:
            descricao_parts.append(f"Competências: {competencias_raw}")
        if linhas_raw:
            descricao_parts.append(f"Linhas: {linhas_raw}")

        return self._build_unit(
            nome=nome,
            instituicao=instituicao,
            uf=location,
            url=url_raw,
            descricao=". ".join(descricao_parts),
            competencias=competencias,
        )

    # ------------------------------------------------------------------
    # Estratégia 2: Scraping paginado
    # ------------------------------------------------------------------

    def _scrape_paginated(self) -> list:
        results = []
        for page_num in range(1, self.MAX_PAGES + 1):
            url = self.BASE_URL if page_num == 1 \
                  else f"{self.BASE_URL}page/{page_num}/"
            try:
                soup = self._fetch_page(url, timeout=30)
            except Exception as e:
                print(f"  Página {page_num}: erro ({e}), parando paginação")
                break

            units = self._parse_listing_page(soup)
            if not units:
                print(f"  Página {page_num}: sem unidades, fim da paginação")
                break

            results.extend(units)
            print(f"  Página {page_num}: {len(units)} unidades")

            # Verifica se há próxima página
            next_link = soup.find("a", class_=re.compile(r"next|próxima", re.I)) or \
                        soup.find("a", string=re.compile(r"›|>>|próxima", re.I))
            if not next_link:
                # Verifica pela numeração de páginas
                page_links = soup.find_all("a", href=re.compile(r"/page/\d+/"))
                max_found = max(
                    (int(re.search(r'/page/(\d+)/', a['href']).group(1))
                     for a in page_links if re.search(r'/page/(\d+)/', a['href'])),
                    default=page_num
                )
                if page_num >= max_found:
                    break

            time.sleep(0.5)  # cortesia para o servidor

        return results

    def _parse_listing_page(self, soup) -> list:
        """Extrai unidades da página de listagem."""
        units = []

        # Tenta encontrar linhas de tabela com link para unidade
        unit_links = soup.find_all("a", href=re.compile(r"/unidades/[^/]+/$"))
        seen_urls = set()

        for link in unit_links:
            href = link.get("href", "").strip()
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)

            nome = self._clean_text(link.get_text())
            if len(nome) < 3:
                continue

            # Tenta coletar dados do contexto do link (linha de tabela/card)
            row_el = link.find_parent(["tr", "li", "div"])
            competencias = []
            uf = ""
            instituicao = ""

            if row_el:
                all_text = self._clean_text(row_el.get_text())
                # Extrai UF (padrão: Cidade/UF no final)
                uf_match = re.search(r'/([A-Z]{2})\s*$', all_text)
                if uf_match:
                    uf = uf_match.group(1)
                # Resto do texto como descrição/competências
                texto_sem_nome = all_text.replace(nome, "").strip()
                if texto_sem_nome:
                    competencias = self._split_competencias(texto_sem_nome)

            # Visita página individual para dados completos
            detail = self._fetch_unit_detail(href, nome)
            if detail:
                units.append(detail)
                continue

            units.append(self._build_unit(
                nome=nome,
                instituicao=instituicao,
                uf=uf,
                url=href,
                descricao="",
                competencias=competencias,
            ))

        return units

    def _fetch_unit_detail(self, url: str, nome_fallback: str) -> dict | None:
        """Visita página individual da unidade para extrair a descrição completa."""
        try:
            soup = self._fetch_page(url, timeout=20)
            text_full = self._clean_text(soup.get_text())

            # Nome da unidade
            h1 = soup.find("h1")
            nome = self._clean_text(h1.get_text()) if h1 else nome_fallback

            # Instituição base
            h2 = soup.find("h2")
            instituicao = self._clean_text(h2.get_text()) if h2 else ""

            # UF
            uf_match = re.search(r'\b([A-Z]{2})\b(?:\s|,|\.)', text_full)
            uf = uf_match.group(1) if uf_match else ""

            # Competências
            competencias = self._extract_competencias_from_page(soup)

            # Descrição completa — prioriza div.description
            desc_div = (
                soup.find("div", class_="description") or
                soup.find("div", class_=re.compile(r"descri(cao|ção|ption)", re.I)) or
                soup.find("div", class_="entry-content") or
                soup.find("article")
            )
            if desc_div:
                # Preserva parágrafos separados por \n\n para segmentação no ETL
                paragrafos = [
                    self._clean_text(el.get_text())
                    for el in desc_div.find_all(["p", "li", "h3", "h4"])
                    if len(el.get_text().strip()) > 10
                ]
                descricao = "\n\n".join(p for p in paragrafos if len(p.split()) > 3)
            else:
                # Fallback: todos os parágrafos da página
                paragrafos = [
                    self._clean_text(p.get_text())
                    for p in soup.find_all("p")
                    if len(p.get_text().strip()) > 40
                ]
                descricao = "\n\n".join(paragrafos)

            return self._build_unit(
                nome=nome,
                instituicao=instituicao,
                uf=uf,
                url=url,
                descricao=descricao,
                competencias=competencias,
            )
        except Exception as e:
            print(f"  Erro ao buscar detalhes de {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_competencias_from_page(self, soup) -> list:
        """Extrai lista de competências a partir de indicadores semânticos."""
        comp_keywords = re.compile(
            r'competênci|compet[eê]nci|linha|área|tecnologia|especiali',
            re.IGNORECASE
        )
        competencias = []

        # Procura seção com título indicativo de competências
        for heading in soup.find_all(["h2", "h3", "h4", "strong"]):
            if comp_keywords.search(heading.get_text()):
                # Coleta itens de lista ou parágrafos seguintes
                sib = heading.find_next_sibling()
                while sib and sib.name in ("ul", "ol", "p"):
                    if sib.name in ("ul", "ol"):
                        for li in sib.find_all("li"):
                            t = self._clean_text(li.get_text())
                            if t:
                                competencias.extend(self._split_competencias(t))
                    sib = sib.find_next_sibling()
                if competencias:
                    break

        # Fallback: lista de itens na página
        if not competencias:
            for li in soup.find_all("li"):
                t = self._clean_text(li.get_text())
                if 10 < len(t) < 120:
                    competencias.extend(self._split_competencias(t))

        return list(dict.fromkeys(competencias))[:15]  # dedup, máx 15

    def _split_competencias(self, text: str) -> list:
        """Divide string de competências em lista canônica."""
        if not text:
            return []
        # Separa por ; / e / ou quebras de linha
        parts = re.split(r'[;/\n|]|(?:\s*,\s*(?=[A-Z]))', text)
        result = []
        for p in parts:
            p = self._clean_text(p).strip(",. ")
            if 4 < len(p) < 100:
                result.append(p)
        return result

    def _build_unit(
        self,
        nome: str,
        instituicao: str,
        uf: str,
        url: str,
        descricao: str,
        competencias: list,
    ) -> dict:
        return {
            "fonte": self.source_name,
            "titulo": nome,
            "unidade_nome": nome,
            "instituicao_base": instituicao,
            "uf": uf,
            "url": url,
            "descricao": descricao,
            "competencias": competencias,
            "status": "FLUXO_CONTINUO",
            "prazo": None,
            "data_extracao": datetime.now().isoformat(),
        }

    def _fetch_raw(self, url: str, timeout: int = 30):
        """Faz requisição HTTP retornando o objeto Response (não BeautifulSoup)."""
        import requests
        try:
            resp = requests.get(url, headers=self.headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            print(f"  Falha ao acessar {url}: {e}")
            return None


if __name__ == "__main__":
    scraper = EMBRAPIIScraper()
    scraper.run()
