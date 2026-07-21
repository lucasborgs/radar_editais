"""Testes do feeder DOU/INLABS (core/ingestion/dou_feeder) — parse + pré-filtro + SearchHit.

Tudo offline: o zip do INLABS é sintetizado em memória (1 XML por matéria, schema
real do INLABS). Login/download não são testados aqui (rede; validados ao vivo
2026-06-09).
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingestion import dou_feeder as dou


def _art(art_type="Aviso de Chamamento Público",
         art_category="Ministério da Ciência, Tecnologia e Inovação/FINEP",
         identifica="CHAMADA PÚBLICA MCTI/FINEP Nº 5/2026",
         ementa="Seleção pública de propostas de inovação.",
         texto="Texto completo da chamada de subvenção econômica."):
    # As CHAVES ficam no schema real do XML do INLABS (camelCase).
    return {"artType": art_type, "artCategory": art_category, "pubDate": "09/06/2026",
            "pdfPage": "https://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?jornal=530&pagina=7",
            "idMateria": "12345", "identifica": identifica, "ementa": ementa,
            "texto": texto}


# ---------------------------------------------------------------------------
# Pré-filtro determinístico
# ---------------------------------------------------------------------------

def test_keeps_chamada_publica():
    assert dou._is_candidate(_art(), None) is True


def test_drops_licitacao_arttype():
    assert dou._is_candidate(_art(art_type="Aviso de Licitação"), None) is False
    assert dou._is_candidate(_art(art_type="Extrato de Contrato"), None) is False
    assert dou._is_candidate(_art(art_type="Resultado de Julgamento"), None) is False


def test_drops_resultado_por_titulo():
    """artType genérico ('Extrato') não pega — o drop por Identifica pega."""
    assert dou._is_candidate(
        _art(art_type="Extrato", identifica="EXTRATO DE TERMO DE FOMENTO Nº 1/2026"),
        None) is False


def test_preserva_extrato_de_edital():
    """'Extrato de edital' é anúncio de chamada aberta — não cai no drop."""
    assert dou._is_candidate(
        _art(art_type="Extrato", identifica="EXTRATO DE EDITAL Nº 3/2026 - CHAMADA"),
        None) is True


def test_exige_sinal_de_fomento():
    assert dou._is_candidate(
        _art(identifica="PORTARIA Nº 9", ementa="Altera o regimento interno.",
             texto="", art_type="Portaria"),
        None) is False


def test_org_allowlist_restringe():
    a = _art()
    assert dou._is_candidate(a, ("Ministério da Ciência",)) is True
    assert dou._is_candidate(a, ("Ministério da Defesa",)) is False


# ---------------------------------------------------------------------------
# SearchHit
# ---------------------------------------------------------------------------

def test_to_hit_carries_agency_and_full_text():
    h = dou._to_hit(_art())
    assert h.title.startswith("CHAMADA PÚBLICA MCTI/FINEP")
    assert h.url.startswith("https://pesquisa.in.gov.br/")   # pdfPage = identidade
    assert h.agency == "Ministério da Ciência, Tecnologia e Inovação"
    assert h.full_text is True                # content já é o Texto — sem full-fetch
    assert h.content == _art()["texto"]


def test_to_hit_fallback_url_e_titulo():
    a = _art()
    a["pdfPage"] = ""
    a["identifica"] = ""
    h = dou._to_hit(a)
    assert h.url == "https://www.in.gov.br/web/dou/-/12345"
    assert h.title == "(sem título)"


# ---------------------------------------------------------------------------
# Parse do zip INLABS
# ---------------------------------------------------------------------------

def _zip_with(*xmls: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, x in enumerate(xmls):
            zf.writestr(f"materia_{i}.xml", x)
    return buf.getvalue()


_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xml>
  <article artType="Aviso de Chamamento Público"
           artCategory="Ministério da Ciência, Tecnologia e Inovação/FINEP"
           pubDate="09/06/2026" pdfPage="https://pesquisa.in.gov.br/p7"
           idMateria="111">
    <body>
      <Identifica>CHAMADA PÚBLICA Nº 5/2026</Identifica>
      <Ementa>Seleção de propostas de &lt;b&gt;inovação&lt;/b&gt;.</Ementa>
      <Texto>&lt;p&gt;Corpo completo   da chamada.&lt;/p&gt;</Texto>
    </body>
  </article>
</xml>"""


def test_iter_articles_parses_and_strips_html():
    arts = list(dou._iter_articles(_zip_with(_XML)))
    assert len(arts) == 1
    a = arts[0]
    assert a["artType"] == "Aviso de Chamamento Público"
    assert a["identifica"] == "CHAMADA PÚBLICA Nº 5/2026"
    # Tags viram espaço (não colam palavras de blocos vizinhos) e whitespace
    # interno é colapsado — daí o espaço solto onde havia tag antes do ponto.
    assert a["ementa"] == "Seleção de propostas de inovação ."
    assert a["texto"] == "Corpo completo da chamada."
    assert a["pdfPage"] == "https://pesquisa.in.gov.br/p7"


def test_iter_articles_skips_broken_xml():
    arts = list(dou._iter_articles(_zip_with("<xml><article", _XML)))
    assert len(arts) == 1
