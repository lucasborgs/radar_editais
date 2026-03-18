"""
Scraper BNDES — Programas de Fomento à Inovação

Estratégia (duas fases independentes):

  Fase 1 — Programas (FLUXO_CONTINUO):
    Processa arquivos HTML/PDF já baixados em bronze_data/bndes_raw/ E
    tenta buscar live URLs do PROGRAM_CATALOG ainda não cobertas.
    Extrai: título, descrição, quem pode solicitar, o que pode ser financiado.
    Três estratégias de extração (tentadas em ordem):
      1. destaques-conteudo  — padrão BNDES portal (ul.destaques-conteudo .aba1/.aba2)
      2. h2-sections         — páginas de chamada/programa (ex: Garagem)
      3. paragraphs-fallback — páginas texto-corrido (ex: Funtec)

  Fase 2 — Chamadas Ativas (ABERTA):
    Acessa páginas dedicadas de chamadas (CHAMADAS_DIRETAS) para capturar
    editais com prazo definido ainda não cobertos pelos arquivos locais.

Saída:
  bndes_programs_{ts}.json  — programas e fundos (fase 1)
  bndes_scan_{ts}.json      — chamadas e editais ativos (fase 2)
"""
import re
import time
from pathlib import Path

from .base import BaseScraper

# ─────────────────────────────────────────────────────────────────────────────
# Catálogo de programas para busca live
# (Inclui programas não cobertos pelos arquivos já baixados)
# ─────────────────────────────────────────────────────────────────────────────
PROGRAM_CATALOG: list[dict] = [
    {
        "nome": "BNDES Mais Inovação",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/programa-bndes-mais-inovacao-investimento",
        "categoria": "inovacao",
    },
    {
        "nome": "BNDES Capital Empreendedor",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/bndes-capital-empreendedor",
        "categoria": "empreendedorismo",
    },
    {
        "nome": "BNDES Funtec",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/bndes-funtec",
        "categoria": "inovacao",
    },
    {
        "nome": "BNDES MPME Inovadora",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/bndes-mpme-inovadora",
        "categoria": "mpme",
    },
    {
        "nome": "Fundo Clima",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima",
        "categoria": "clima",
    },
    {
        "nome": "Fundo Clima - Transição Energética",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima/fundo-clima-transicao-energetica",
        "categoria": "clima",
    },
    {
        "nome": "Fundo Clima - Indústria Verde",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima/fundo-clima-industria-verde",
        "categoria": "clima",
    },
    {
        "nome": "Fundo Clima - Máquinas Verdes",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima/fundo-clima-maquinas-verdes",
        "categoria": "clima",
    },
    {
        "nome": "Fundo Clima - Desenvolvimento Urbano",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima/fundo-clima-desenvolvimento-urbano-resiliente-sustentavel",
        "categoria": "clima",
    },
    {
        "nome": "Fundo Clima - Florestas Nativas",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima/fundo-clima-florestas-nativas-recursos-hidricos",
        "categoria": "clima",
    },
    {
        "nome": "Fundo Clima - Logística Verde",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima/fundo-clima-logistica-transporte-coletivo-mobilidade-verde",
        "categoria": "clima",
    },
    {
        "nome": "Fundo Clima - Serviços e Inovação Verdes",
        "url": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/fundo-clima/servicos-inovacao-verdes",
        "categoria": "clima",
    },
]

# Páginas dedicadas de chamadas ativas (fase 2)
CHAMADAS_DIRETAS: list[tuple[str, str]] = [
    ("inovacao", "https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/inovacao/editais-e-chamadas"),
    ("inovacao", "https://www.bndes.gov.br/wps/portal/site/home/financiamento/chamadas-publicas"),
]

# Keywords que identificam chamada real com prazo (fase 2)
CHAMADA_KEYWORDS = [
    "chamada", "edital", "seleção", "inscrição", "prazo",
    "resultado", "encerrada", "encerrado", "convocação", "homologação",
]

# Mapeamento título → categoria (para arquivos locais sem entrada no catálogo)
_CATEGORIA_RE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"fundo clima|clima|sustent|verde|floresta|transição energética", re.I), "clima"),
    (re.compile(r"garagem|empreendedor|startup|aceleração", re.I), "empreendedorismo"),
    (re.compile(r"mpme|micro|pequen|médias empresa", re.I), "mpme"),
    (re.compile(r"funtec|pesquisa aplicada|p&d|desenvolvimento tecnológico", re.I), "inovacao"),
    (re.compile(r"mais inovação|capital inovação|inovação", re.I), "inovacao"),
]


def _infer_categoria(title: str) -> str:
    for pattern, cat in _CATEGORIA_RE:
        if pattern.search(title):
            return cat
    return "inovacao"


class BNDESScraper(BaseScraper):
    """Scraper BNDES: programas de fomento (HTML/PDF locais + live) + chamadas ativas."""

    def __init__(self):
        super().__init__(source_name="BNDES", output_subdir="bndes_raw")

    # ─────────────────────────────────────────────────────────────────────────
    # Ponto de entrada
    # ─────────────────────────────────────────────────────────────────────────

    def extract(self) -> list:
        all_results = []

        # ── Fase 1: Programas ────────────────────────────────────────────────
        programs = self._extract_programs()
        if programs:
            self._save(programs, prefix="bndes_programs")
            all_results.extend(programs)
            print(f"  [BNDES] Fase 1: {len(programs)} programas extraídos")

        # ── Fase 2: Chamadas ativas ──────────────────────────────────────────
        chamadas = self._extract_chamadas()
        if chamadas:
            self._save(chamadas, prefix="bndes_scan")
            all_results.extend(chamadas)
            print(f"  [BNDES] Fase 2: {len(chamadas)} chamadas ativas")

        return all_results

    # ─────────────────────────────────────────────────────────────────────────
    # Fase 1 — Programas
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_programs(self) -> list:
        results = []
        seen_urls: set = set()

        # 1a. Arquivos locais já baixados
        for item in self._process_local_files():
            url = item.get("url", "")
            key = url or item.get("titulo", "")
            if key and key not in seen_urls:
                seen_urls.add(key)
                results.append(item)

        # 1b. Live fetch do catálogo (apenas URLs não cobertas)
        for entry in PROGRAM_CATALOG:
            url = entry["url"]
            if url in seen_urls:
                continue
            print(f"  [BNDES/live] Buscando: {entry['nome']}")
            item = self._fetch_and_parse_program(url, entry["nome"], entry["categoria"])
            if item:
                seen_urls.add(url)
                results.append(item)
            time.sleep(0.5)

        return results

    def _process_local_files(self) -> list:
        """Processa arquivos HTML e PDF já baixados em bndes_raw/."""
        results = []
        raw_dir = Path(self.output_dir)

        html_files = sorted(raw_dir.glob("*.html"))
        pdf_files  = sorted(raw_dir.glob("*.pdf"))

        print(f"  [BNDES/local] {len(html_files)} HTML(s), {len(pdf_files)} PDF(s)")

        for html_file in html_files:
            try:
                item = self._parse_html_file(html_file)
                if item:
                    results.append(item)
                    print(f"    ✓ {item['titulo'][:70]}")
            except Exception as e:
                print(f"    ✗ {html_file.name}: {e}")

        for pdf_file in pdf_files:
            try:
                item = self._parse_pdf_file(pdf_file)
                if item:
                    results.append(item)
                    print(f"    ✓ PDF: {item['titulo'][:70]}")
            except Exception as e:
                print(f"    ✗ {pdf_file.name}: {e}")

        return results

    def _parse_html_file(self, filepath: Path) -> dict | None:
        """Parseia um HTML local de programa BNDES."""
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()

        soup = self._soup_from_string(content)
        if not soup:
            return None

        title = self._extract_title(soup)
        if not title or "(expirado)" in title.lower():
            return None

        url      = self._extract_canonical_url(soup)
        descricao = self._extract_meta_description(soup)
        quem, financia = self._extract_content_sections(soup)
        raw_html = self._extract_content_raw_html(soup)
        status   = self._infer_status(title, quem + financia)
        categoria = _infer_categoria(title)

        return self._build_record(title, url, descricao, quem, financia, status, categoria, raw_html=raw_html)

    def _parse_pdf_file(self, filepath: Path) -> dict | None:
        """Extrai texto de PDF via pdfplumber e cria registro."""
        try:
            import pdfplumber
        except ImportError:
            print("    pdfplumber não instalado — pip install pdfplumber")
            return None

        try:
            pages_text = []
            with pdfplumber.open(str(filepath)) as pdf:
                for page in pdf.pages[:8]:
                    t = page.extract_text() or ""
                    pages_text.append(t)
                    if sum(len(p) for p in pages_text) > 6000:
                        break
            full_text = "\n".join(pages_text).strip()
        except Exception as e:
            print(f"    pdfplumber falhou em {filepath.name}: {e}")
            return None

        if len(full_text) < 50:
            return None

        # Título: primeira linha não-vazia e de tamanho razoável
        title = next(
            (l.strip() for l in full_text.splitlines() if 10 < len(l.strip()) < 120),
            filepath.stem,
        )

        return self._build_record(
            titulo=title,
            url="",
            descricao=full_text[:500],
            quem_pode="",
            o_que_financia=full_text[:3000],
            status="FLUXO_CONTINUO",
            categoria=_infer_categoria(title),
        )

    def _fetch_and_parse_program(self, url: str, nome: str, categoria: str) -> dict | None:
        """Faz GET de um URL de programa e parseia."""
        try:
            soup = self._fetch_page(url, timeout=25)
        except Exception as e:
            print(f"    Falha em {nome}: {e}")
            return None

        title    = self._extract_title(soup) or nome
        descricao = self._extract_meta_description(soup)
        quem, financia = self._extract_content_sections(soup)
        raw_html = self._extract_content_raw_html(soup)
        status   = self._infer_status(title, quem + financia)

        return self._build_record(title, url, descricao, quem, financia, status, categoria, raw_html=raw_html)

    # ─────────────────────────────────────────────────────────────────────────
    # Extração de conteúdo — 3 estratégias
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_content_sections(self, soup) -> tuple[str, str]:
        """Retorna (quem_pode_solicitar, o_que_pode_ser_financiado)."""
        # Estratégia 1: ul.destaques-conteudo .aba1/.aba2 (padrão portal produto)
        conteudo = soup.find("ul", class_="destaques-conteudo")
        if conteudo:
            aba1 = conteudo.find("li", class_="aba1")
            aba2 = conteudo.find("li", class_="aba2")
            quem     = self._clean_text(aba1.get_text(" ", strip=True)) if aba1 else ""
            financia = self._clean_text(aba2.get_text(" ", strip=True)) if aba2 else ""
            if quem or financia:
                return quem, financia

        # Estratégia 2: h2/h3 semânticos (Garagem e chamadas)
        quem, financia = self._extract_via_h2_sections(soup)
        if quem or financia:
            return quem, financia

        # Estratégia 3: parágrafos texto-corrido (Funtec)
        return self._extract_via_paragraphs(soup)

    def _extract_via_h2_sections(self, soup) -> tuple[str, str]:
        QUEM_KW = re.compile(r"quem pode|público.alvo|elegibili|beneficiári|quem participa", re.I)
        FIN_KW  = re.compile(r"o que (pode|é|financia)|apoio|produto|financi|benefício", re.I)

        quem_parts: list[str] = []
        fin_parts:  list[str] = []

        for h in soup.find_all(["h2", "h3"]):
            heading = h.get_text(strip=True)
            sib = h.find_next_sibling()
            if not sib:
                continue
            content = self._clean_text(sib.get_text(" ", strip=True))
            if not content or len(content) < 20:
                continue

            if QUEM_KW.search(heading):
                quem_parts.append(content)
            elif FIN_KW.search(heading):
                fin_parts.append(content)

        return " ".join(quem_parts)[:2000], " ".join(fin_parts)[:2000]

    def _extract_content_raw_html(self, soup) -> str:
        """Retorna HTML bruto das seções de conteúdo para preservação estrutural."""
        # Prioridade 1: ul.destaques-conteudo (padrão portal produto BNDES)
        conteudo = soup.find("ul", class_="destaques-conteudo")
        if conteudo:
            return conteudo.decode()
        # Prioridade 2: elemento principal da página
        main = soup.find("main") or soup.find("article") or soup.find("div", {"id": "main"})
        if main:
            for noise in main.find_all(["script", "style", "nav", "footer"]):
                noise.decompose()
            return main.decode_contents()[:30000]
        return ""

    def _extract_via_paragraphs(self, soup) -> tuple[str, str]:
        """Fallback: parágrafos longos usados como descrição geral."""
        paras = [
            self._clean_text(p.get_text(" ", strip=True))
            for p in soup.find_all("p", attrs={"dir": "ltr"})
            if len(p.get_text(strip=True)) > 60
        ]
        if not paras:
            paras = [
                self._clean_text(p.get_text(" ", strip=True))
                for p in soup.find_all("p")
                if len(p.get_text(strip=True)) > 80
            ]
        # Sem separação quem/financia — usa tudo como corpo descritivo
        return "", " ".join(paras[:10])[:3000]

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers de metadados HTML
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_title(self, soup) -> str:
        meta = soup.find("meta", attrs={"name": "twitter:title"})
        if meta and meta.get("content"):
            return meta["content"].strip()
        tag = soup.find("title")
        if tag:
            return re.sub(r"\s*[-–|]\s*BNDES\s*$", "", tag.get_text().strip(), flags=re.I).strip()
        return ""

    def _extract_meta_description(self, soup) -> str:
        meta = soup.find("meta", attrs={"property": "description"})
        return (meta.get("content") or "").strip() if meta else ""

    def _extract_canonical_url(self, soup) -> str:
        tag = soup.find("link", attrs={"rel": "canonical"})
        if tag and tag.get("href"):
            return tag["href"].replace("http://", "https://")
        return ""

    def _soup_from_string(self, html_content: str):
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html_content, "html.parser")
        except Exception:
            return None

    def _infer_status(self, title: str, content: str) -> str:
        text = (title + " " + content).lower()
        if "(expirado)" in text or "encerrad" in text:
            return "ENCERRADA"
        if "chamada pública" in text or "seleção de empreendedores" in text:
            return "ABERTA"
        return "FLUXO_CONTINUO"

    # ─────────────────────────────────────────────────────────────────────────
    # Fase 2 — Chamadas Ativas
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_chamadas(self) -> list:
        results = []
        seen_urls: set = set()

        for categoria, url in CHAMADAS_DIRETAS:
            print(f"  [BNDES/chamadas] {url}")
            try:
                soup = self._fetch_page(url, timeout=30)
            except Exception as e:
                print(f"    Erro: {e}")
                continue

            for link in soup.find_all("a", href=True):
                text = self._clean_text(link.get_text())
                if not text or len(text) < 5:
                    continue

                full_url = self._normalize_url(link["href"], url)
                if not full_url or full_url in seen_urls:
                    continue

                text_l = text.lower()
                url_l  = full_url.lower()
                if not any(k in text_l or k in url_l for k in CHAMADA_KEYWORDS):
                    continue

                seen_urls.add(full_url)
                parent  = link.find_parent(["p", "div", "li", "td", "tr"])
                contexto = self._clean_text(parent.get_text()) if parent else text
                status  = self._determine_status_chamada(contexto)

                results.append({
                    "fonte": "BNDES",
                    "titulo": text,
                    "url": full_url,
                    "descricao": "",
                    "quem_pode_solicitar": "",
                    "o_que_financia": "",
                    "contexto_capturado": contexto[:300],
                    "status": status,
                    "programa": categoria,
                    "data_extracao": self._get_date(),
                })
                print(f"    [{status}] {text[:60]}")

        return results

    def _determine_status_chamada(self, contexto: str) -> str:
        c = contexto.lower()
        if "aberta" in c or "inscrições" in c or "abertas" in c:
            return "ABERTA"
        if "encerrad" in c or "finalizad" in c:
            return "ENCERRADA"
        return "ABERTA"

    def _normalize_url(self, href: str, base_url: str) -> str:
        if href.startswith("/"):
            return f"https://www.bndes.gov.br{href}"
        if href.startswith("?"):
            return f"{base_url}{href}"
        if href.startswith("http"):
            return href
        return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Builder de registro
    # ─────────────────────────────────────────────────────────────────────────

    def _build_record(
        self,
        titulo: str,
        url: str,
        descricao: str,
        quem_pode: str,
        o_que_financia: str,
        status: str,
        categoria: str,
        raw_html: str = "",
    ) -> dict:
        partes = [p for p in [descricao, quem_pode, o_que_financia] if p]
        contexto = " | ".join(partes)
        return {
            "fonte": "BNDES",
            "titulo": titulo,
            "url": url,
            "descricao": descricao,
            "quem_pode_solicitar": quem_pode,
            "o_que_financia": o_que_financia,
            "contexto_capturado": contexto[:500],
            "raw_html": raw_html,
            "status": status,
            "programa": categoria,
            "data_extracao": self._get_date(),
        }


if __name__ == "__main__":
    scraper = BNDESScraper()
    scraper.run()
