"""Gates herméticos de conteúdo dos três incidentes (quatro queries)."""
from __future__ import annotations

import json
import unicodedata

import pytest

from radar.core.config import ROOT

pytestmark = pytest.mark.unit


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    return "".join(c for c in value if not unicodedata.combining(c)).casefold()


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


_finep_golden = ROOT / "data/silver/structured_docs/finep/745.jsonl"
_fapesc_golden = ROOT / "data/silver/structured_docs/fapesc/31-2026.jsonl"


@pytest.mark.skipif(not _finep_golden.exists(), reason="structured docs não gerados (rode o structurer primeiro)")
def test_finep_golden_usa_terceira_rerratificacao_vigente():
    blocks = _jsonl(_finep_golden)
    current = [
        b for b in blocks
        if "3ª_rerratificação_-_Regulamento_rerratificado.pdf" == b.get("doc")
        and any("4.3" in s or "4.5" in s for s in (b.get("section_path") or []))
    ]
    text = _fold("\n".join(b["text"] for b in current))
    assert "pagamento de pessoal" in text
    assert "4.3" in text
    assert any("4.5" in " ".join(b.get("section_path") or []) for b in current)
    assert "diarias e despesas com locomocao" not in text
    assert "servicos de terceiros" not in text


@pytest.mark.skipif(not _fapesc_golden.exists(), reason="structured docs não gerados (rode o structurer primeiro)")
def test_fapesc_golden_cobre_as_quatro_familias_e_thresholds():
    blocks = _jsonl(_fapesc_golden)
    section4 = [
        b for b in blocks
        if (b.get("section_path") or [""])[0].startswith("4.")
    ]
    text = _fold("\n".join(b["text"] for b in section4))
    for required in (
        "empresa proponente", "coordenador", "equipe tecnica", "proposta de projeto",
        "1 (um) ano", "r$ 1.200.000,00", "minima de 5%", "maior ou igual a 2",
        "r$ 80.000,00", "ate 12 (doze) meses", "empresario individual",
        "microempreendedor individual",
    ):
        assert _fold(required) in text, required
    assert "preferencialmente, ter participado" in text


def test_barn_golden_esta_preservado_no_catalogo_curado():
    payload = json.loads(
        (ROOT / "data/silver/investidores.json").read_text(encoding="utf-8")
    )
    barn = next(item for item in payload["investidores"] if item["id"] == "investidor:barn-invest")
    assert barn["setores"] == [
        "agro e uso da terra",
        "transporte e mobilidade",
        "indústria limpa e economia circular",
        "energia renovável e eficiência energética",
    ]
    thesis = _fold(barn["tese"])
    assert "greentech" in thesis and "transicao verde" in thesis
    assert "america latina" in thesis


def test_golden_declara_casos_sem_compensacao_por_media():
    golden = json.loads(
        (ROOT / "data/evaluation/golden/explore.json").read_text(encoding="utf-8")
    )
    assert [c["id"] for c in golden["cases"]] == [
        "finep-745-itens-financiaveis",
        "fapesc-31-2026-admissibilidade",
        "barn-verticais",
        "barn-tese",
        "iforestal-profile-strategy",
    ]
    assert all(c["assertions"]["required"] for c in golden["cases"])
    assert all(c["assertions"]["forbidden"] for c in golden["cases"])
