"""Testes do scraper L0 FAPESC — parsing de HTML + seleção do PDF do edital.

Foca nos parsers puros (_native_id, _find_edital_pdf, _is_boilerplate,
_uploads_recency) e na orquestração de _parse_chamada com HTML/PDF mockados.
Não faz HTTP real nem abre PDFs de verdade.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from radar.pipeline.extractors.fapesc import FAPESCScraper  # noqa: E402

pytestmark = pytest.mark.unit

# =============================================================================
# _native_id — número-ano do slug WordPress
# =============================================================================

@pytest.mark.parametrize("url,expected", [
    ("https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-37-2026-x", "37-2026"),
    ("https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-004-2026-y", "4-2026"),
    ("https://fapesc.sc.gov.br/edital-...-no-54-2024-z", "54-2024"),
])
def test_native_id_extracts_num_year(url: str, expected: str):
    assert FAPESCScraper._native_id(url) == expected


def test_native_id_none_when_absent():
    assert FAPESCScraper._native_id("https://fapesc.sc.gov.br/edital-sem-numero") is None


# =============================================================================
# _is_boilerplate / _uploads_recency
# =============================================================================

def test_is_boilerplate_matches_skiplist():
    skip = ["plano-de-integridade", "codigo_conduta", "resultado"]
    base = "https://fapesc.sc.gov.br/wp-content/uploads/2026/01/"
    assert FAPESCScraper._is_boilerplate(base + "Plano-de-Integridade-e-Compliance.pdf", skip)
    assert FAPESCScraper._is_boilerplate(base + "codigo_conduta_2024.pdf", skip)
    assert FAPESCScraper._is_boilerplate(base + "Resultado-Adm-Preliminar.pdf", skip)
    assert not FAPESCScraper._is_boilerplate(base + "CP-04_2026.pdf", skip)


def test_uploads_recency_orders_by_path_date():
    older = "https://fapesc.sc.gov.br/wp-content/uploads/2026/01/a.pdf"
    newer = "https://fapesc.sc.gov.br/wp-content/uploads/2026/06/b.pdf"
    assert FAPESCScraper._uploads_recency(newer) > FAPESCScraper._uploads_recency(older)
    assert FAPESCScraper._uploads_recency("https://x/no-uploads-path.pdf") == 0


# =============================================================================
# _find_edital_pdf — heurística âncora "edital" + fallback recência + skip-list
# =============================================================================
# Fixture espelha a estrutura real (recon 2026-06-18): boilerplate institucional,
# o edital ("ACESSE O EDITAL COMPLETO"), manuais e peças de resultado.

from bs4 import BeautifulSoup  # noqa: E402

_PAGE_HTML = """
<html><body>
<a href="https://fapesc.sc.gov.br/wp-content/uploads/2023/06/Plano-de-Integridade-e-Compliance-da-Fapesc.pdf">Plano de Integridade e Compliance</a>
<a href="https://fapesc.sc.gov.br/wp-content/uploads/2024/11/codigo_conduta_2024.pdf">Código de Conduta</a>
<a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/01/CP-04_2026.pdf">ACESSE O EDITAL COMPLETO</a>
<a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/05/MANUAL_Cadastramento.pdf">CADASTRO NO SIGFAPESC</a>
<a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/06/Resultado-Final.pdf">RESULTADO FINAL</a>
</body></html>
"""


def test_find_edital_pdf_prefers_edital_anchor():
    scraper = FAPESCScraper()
    soup = BeautifulSoup(_PAGE_HTML, "html.parser")
    with patch("radar.pipeline.extractors.fapesc._skip_keywords",
               return_value=["plano-de-integridade", "codigo_conduta", "resultado",
                             "manual", "cadastr", "sigfapesc"]):
        url = scraper._find_edital_pdf(soup)
    # NÃO o resultado (mais recente) nem o boilerplate — o edital, pela âncora.
    assert url.endswith("/CP-04_2026.pdf")


def test_find_edital_pdf_falls_back_to_recent_non_boilerplate():
    """Sem âncora 'edital', pega o PDF não-boilerplate mais recente."""
    html = """
    <html><body>
    <a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/01/codigo_conduta_2024.pdf">Código de Conduta</a>
    <a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/02/Anexo-A.pdf">Anexo A</a>
    <a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/05/Anexo-B.pdf">Anexo B</a>
    </body></html>
    """
    scraper = FAPESCScraper()
    with patch("radar.pipeline.extractors.fapesc._skip_keywords",
               return_value=["codigo_conduta"]):
        url = scraper._find_edital_pdf(BeautifulSoup(html, "html.parser"))
    assert url.endswith("/Anexo-B.pdf")  # mais recente, não-boilerplate


def test_find_edital_pdf_none_when_no_pdf():
    scraper = FAPESCScraper()
    soup = BeautifulSoup("<html><body><a href='/sem-pdf'>x</a></body></html>", "html.parser")
    assert scraper._find_edital_pdf(soup) is None


def test_normative_pdfs_compose_base_and_retification():
    html = """
    <a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/01/Edital-31.pdf">ACESSE O EDITAL COMPLETO</a>
    <a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/02/Retificacao-1.pdf">1ª Retificação</a>
    <a href="https://fapesc.sc.gov.br/wp-content/uploads/2026/03/Resultado.pdf">Resultado</a>
    """
    scraper = FAPESCScraper()
    with patch("radar.pipeline.extractors.fapesc._skip_keywords", return_value=["resultado"]):
        docs = scraper._find_normative_pdfs(BeautifulSoup(html, "html.parser"))
    assert docs == [
        ("https://fapesc.sc.gov.br/wp-content/uploads/2026/01/Edital-31.pdf", "edital-base"),
        ("https://fapesc.sc.gov.br/wp-content/uploads/2026/02/Retificacao-1.pdf", "emenda"),
    ]


# =============================================================================
# _parse_chamada — orquestração: HTML → PDF → texto_cru + prazo do PDF
# =============================================================================

# Texto que SIMULA o conteúdo do PDF do edital (tem os marcadores que o resumo
# HTML não tinha — é o teste de que pegamos o edital, não o resumo).
_FAKE_EDITAL_TEXT = (
    "EDITAL DE CHAMADA PÚBLICA FAPESC N.º 04/2026\n\n"
    "1. DO OBJETO\nApoio a projetos de proponente sediado em SC.\n\n"
    "2. CRONOGRAMA\nSubmissão de propostas até 17/06/2026.\n\n"
    "3. DOS RECURSOS\nValor global de R$ 2.000.000,00.\n\n"
    "ANEXO I — Formulário."
)


def test_parse_chamada_uses_pdf_text():
    scraper = FAPESCScraper()
    url = "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-004-2026-x"
    with patch.object(scraper, "_fetch", return_value=_PAGE_HTML), \
         patch.object(scraper, "_extract_pdf_text", return_value=_FAKE_EDITAL_TEXT):
        row = scraper._parse_chamada(url, "Propriedade Intelectual")

    assert row is not None
    assert row["content_source"] == "pdf"
    assert row["edital_pdf_url"].endswith("/CP-04_2026.pdf")
    assert row["native_id"] == "4-2026"
    # marcadores do edital (ausentes no resumo HTML)
    for marker in ("proponente", "cronograma", "R$", "anexo"):
        assert marker.lower() in row["texto_cru"].lower()
    # prazo extraído do texto do PDF
    assert row["data_limite"] == "17/06/2026"
    assert row["status"] == "ABERTA"
    assert row["fluxo_continuo"] is False
    assert datetime.fromisoformat(row["data_extracao"]).tzinfo is not None


def test_parse_chamada_ignores_signature_far_future_date():
    """Rodapé de assinatura SGP-e ('válido até 15/04/2126') não vira data_limite."""
    from datetime import date
    yr = date.today().year
    pdf_text = (
        f"Submissão de propostas até 30/09/{yr + 1}.\n\n"
        f'Assinado por: "SGP-e", emitido em 15/04/{yr} e válido até 15/04/2126 - código.'
    )
    scraper = FAPESCScraper()
    url = "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-004-2026-x"
    with patch.object(scraper, "_fetch", return_value=_PAGE_HTML), \
         patch.object(scraper, "_extract_pdf_text", return_value=pdf_text):
        row = scraper._parse_chamada(url, "x")
    assert row["data_limite"] == f"30/09/{yr + 1}"  # não 15/04/2126


def test_parse_chamada_falls_back_to_html_when_pdf_empty():
    """PDF não extrai → usa o resumo HTML, content_source=html, sem perder a linha."""
    scraper = FAPESCScraper()
    html = _PAGE_HTML + "<div>Resumo da chamada de propriedade intelectual.</div>"
    url = "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-004-2026-x"
    with patch.object(scraper, "_fetch", return_value=html), \
         patch.object(scraper, "_extract_pdf_text", return_value=""):
        row = scraper._parse_chamada(url, "PI")
    assert row is not None
    assert row["content_source"] == "html"
    assert row["texto_cru"]  # não vazio (caiu no resumo)


def test_parse_chamada_handles_request_failure():
    """Fetch da página crasha → retorna None silenciosamente."""
    import requests as req
    scraper = FAPESCScraper()
    with patch.object(scraper, "_fetch", side_effect=req.ConnectionError("offline")):
        row = scraper._parse_chamada("https://fapesc.sc.gov.br/edital-x", "x")
    assert row is None


# =============================================================================
# _enumerate_chamadas — dedup + filtro de não-editais
# =============================================================================

_LISTAGEM_FIXTURE = """
<html><body>
<a href="https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-04-2026-pi">Edital 04/2026 PI</a>
<a href="https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-04-2026-pi">leia mais</a>
<a href="https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-16-2026-acafe">Edital 16/2026 ACAFE</a>
<a href="https://fapesc.sc.gov.br/category/chamadas-abertas/">Categoria</a>
<a href="https://fapesc.sc.gov.br/sobre">Sobre</a>
</body></html>
"""


def test_enumerate_chamadas_dedupes_and_keeps_longest_title():
    scraper = FAPESCScraper()
    with patch.object(scraper, "_fetch", return_value=_LISTAGEM_FIXTURE):
        items = scraper._enumerate_chamadas()
    urls = [u for u, _ in items]
    assert urls == [
        "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-04-2026-pi",
        "https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-16-2026-acafe",
    ]
    # título mais longo vence "leia mais"
    titulo_pi = dict(items)["https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-04-2026-pi"]
    assert titulo_pi == "Edital 04/2026 PI"
    # links que não são página de edital (categoria, menu) não entram
    assert all("/category/" not in u and "/sobre" not in u for u in urls)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
