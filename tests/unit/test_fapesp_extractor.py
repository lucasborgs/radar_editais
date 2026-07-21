"""
Testes do scraper L0 FAPESP — parsing de HTML.

Foca nos parsers puros (_clean_text, _follow_meta_refresh, _br_to_iso,
_parse_chamada com HTML mockado). Não faz HTTP real.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from radar.pipeline.extractors.fapesp import FAPESPScraper  # noqa: E402

pytestmark = pytest.mark.unit

# =============================================================================
# _clean_text — duplo-unescape + strip tags (gotcha §8 wikis/fapesp.md)
# =============================================================================

def test_clean_text_double_unescape():
    """`&amp;ccedil;` → `&ccedil;` → `ç` (FAPESP HTML duplo-escape)."""
    raw = "Modalidade: PIPE &amp;ndash; Fase 1"
    out = FAPESPScraper._clean_text(raw)
    assert "PIPE – Fase 1" in out or "PIPE - Fase 1" in out


def test_clean_text_strips_html_tags():
    raw = "<p>texto <b>importante</b></p><script>evil()</script>"
    out = FAPESPScraper._clean_text(raw)
    assert "evil" not in out
    assert "texto" in out
    assert "importante" in out
    assert "<" not in out


def test_clean_text_collapses_blank_lines():
    raw = "linha1\n\n\n\n\nlinha2"
    out = FAPESPScraper._clean_text(raw)
    assert "linha1\n\nlinha2" == out


# =============================================================================
# _br_to_iso — conversão de prazo
# =============================================================================

@pytest.mark.parametrize("br,iso", [
    ("17/06/2026", "2026-06-17"),
    ("01/01/2027", "2027-01-01"),
    ("31/12/2025", "2025-12-31"),
])
def test_br_to_iso_valid(br: str, iso: str):
    assert FAPESPScraper._br_to_iso(br) == iso


@pytest.mark.parametrize("bad", ["32/13/2026", "abc", "", "17/06"])
def test_br_to_iso_invalid_returns_none(bad: str):
    assert FAPESPScraper._br_to_iso(bad) is None


# =============================================================================
# _follow_meta_refresh — `/{id}` → `/{id}/{slug}`
# =============================================================================

def test_follow_meta_refresh_extracts_target():
    scraper = FAPESPScraper()
    html = '<html><head><meta http-equiv="refresh" content="0; url=\'https://fapesp.br/18064/slug-aqui\'"/></head></html>'
    out = scraper._follow_meta_refresh(html, "https://fapesp.br/18064")
    assert out == "https://fapesp.br/18064/slug-aqui"


def test_follow_meta_refresh_absolute_path():
    scraper = FAPESPScraper()
    html = '<meta http-equiv="refresh" content="0; url=/18064/slug"/>'
    out = scraper._follow_meta_refresh(html, "https://fapesp.br/18064")
    assert out == "https://fapesp.br/18064/slug"


def test_follow_meta_refresh_no_meta_returns_fallback():
    scraper = FAPESPScraper()
    html = '<html><body>página sem meta-refresh</body></html>'
    out = scraper._follow_meta_refresh(html, "https://fapesp.br/18064")
    assert out == "https://fapesp.br/18064"


# =============================================================================
# _parse_chamada — extração de modalidade/areas/prazo de HTML real
# =============================================================================

_HTML_PIPE_AGRO = """
<html><head><title>Chamada PIPE Agro</title></head>
<body>
<div class="conteudo">
<h1>Chamada PIPE Jornada Tecnológica Agro – Fase 1</h1>
<p>Modalidade de Apoio: PIPE - Fase 1</p>
<p>Prazo para apresentação da proposta completa para propostas enquadradas: 17/06/2026</p>
<p>Áreas: Agro</p>
<p>O valor máximo de financiamento previsto por projeto para a presente Chamada é de R$ 500.000,00.</p>
</div>
</body></html>
"""


def test_parse_chamada_extracts_modalidade_and_prazo():
    scraper = FAPESPScraper()
    with patch.object(scraper, "_fetch_chamada_page", return_value=_HTML_PIPE_AGRO):
        row = scraper._parse_chamada("https://fapesp.br/18064/agro", "PIPE Agro")
    assert row is not None
    assert "PIPE" in (row["modalidades"] or "")
    assert row["data_limite"] == "2026-06-17"
    assert row["status"] == "ABERTA"
    assert row["fluxo_continuo"] is False
    assert "PIPE Agro" == row["titulo"]


_HTML_FLUXO_CONTINUO = """
<html><body>
<p>Modalidade: Bolsa de Pesquisa em Pequena Empresa</p>
<p>Submissão em fluxo contínuo</p>
<p>Áreas: todas</p>
</body></html>
"""


def test_parse_chamada_detects_fluxo_continuo():
    scraper = FAPESPScraper()
    with patch.object(scraper, "_fetch_chamada_page", return_value=_HTML_FLUXO_CONTINUO):
        row = scraper._parse_chamada("https://fapesp.br/99999/cont", "Cont")
    assert row["fluxo_continuo"] is True
    assert row["status"] == "FLUXO_CONTINUO"
    assert row["data_limite"] is None


def test_parse_chamada_handles_request_failure():
    """Se fetch crashar, retornar None silenciosamente — caller filtra."""
    import requests as req
    scraper = FAPESPScraper()
    with patch.object(scraper, "_fetch_chamada_page", side_effect=req.ConnectionError("offline")):
        row = scraper._parse_chamada("https://fapesp.br/x/y", "x")
    assert row is None


# =============================================================================
# _enumerate_chamadas — parsing do accordion da listagem
# =============================================================================

_LISTAGEM_FIXTURE = """
<html><body>
<div class="accordion">
  <h3><a href="https://fapesp.br/18064">PIPE Agro</a></h3>
  <h3><a href="https://fapesp.br/18069">ESPCA 20ª</a></h3>
  <h3><a href="https://fapesp.br/18067">Auxílio à Inovação Regular</a></h3>
  <h3><a href="https://fapesp.br/18064">DUPLICATA — não conta</a></h3>
  <h3><a href="https://fapesp.br/menu-sem-id">Link de menu</a></h3>
</div>
</body></html>
"""


def test_enumerate_chamadas_dedupes_and_filters_menu_links():
    scraper = FAPESPScraper()
    with patch.object(scraper, "_fetch_decoded", return_value=_LISTAGEM_FIXTURE):
        items = scraper._enumerate_chamadas()
    urls = [u for u, _ in items]
    assert urls == [
        "https://fapesp.br/18064",
        "https://fapesp.br/18069",
        "https://fapesp.br/18067",
    ]
    # Menu sem ID NÃO entra
    assert all("/menu-sem-id" not in u for u in urls)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
