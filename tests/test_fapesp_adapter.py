"""
Testes do adapter L1 FAPESP — leitura do bronze + Documento Canônico
(estratégia §12.4 `html_body`).

Mockamos BRONZE_DIR via monkeypatch pra criar fixtures isoladas — não
dependem do bronze real do projeto.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


def _make_bronze(tmp_path: Path, chamadas: list[dict]) -> Path:
    """Cria bronze_dir temporário com 1 arquivo JSON. Retorna caminho do raiz."""
    raw_dir = tmp_path / "fapesp_raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "fapesp_scan_20260529_000000.json").write_text(
        json.dumps(chamadas, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def _patch_bronze(monkeypatch, fake_root: Path):
    """Aponta o adapter para o bronze fake."""
    import pipeline.adapters.fapesp as fapesp_mod
    monkeypatch.setattr(fapesp_mod, "_BRONZE_DIR", fake_root / "fapesp_raw")


@pytest.fixture
def adapter():
    from pipeline.adapters.fapesp import Adapter
    return Adapter()


# =============================================================================
# native_id extraction
# =============================================================================

def test_extract_native_id_basic():
    from pipeline.adapters.fapesp import _extract_native_id
    assert _extract_native_id("https://fapesp.br/18064") == "18064"
    assert _extract_native_id("https://fapesp.br/18064/slug") == "18064"
    assert _extract_native_id("http://www.fapesp.br/18064/") == "18064"
    assert _extract_native_id("") is None
    assert _extract_native_id("https://fapesp.br/sem-id-aqui") is None


# =============================================================================
# to_documents — happy path
# =============================================================================

def test_to_documents_returns_html_body_unit(tmp_path: Path, monkeypatch, adapter):
    bronze = _make_bronze(tmp_path, [
        {"url": "https://fapesp.br/18064/slug", "titulo": "PIPE Agro",
         "texto_cru": "Modalidade de Apoio: PIPE - Fase 1\nPrazo: 17/06/2026"},
    ])
    _patch_bronze(monkeypatch, bronze)

    docs = adapter.to_documents("18064")
    assert len(docs) == 1
    assert docs[0]["doc_name"] == "pagina-chamada"
    assert len(docs[0]["units"]) == 1
    assert "PIPE - Fase 1" in docs[0]["units"][0]


def test_to_documents_normalizes_http_to_https(tmp_path: Path, monkeypatch, adapter):
    bronze = _make_bronze(tmp_path, [
        {"url": "http://www.fapesp.br/18064", "titulo": "PIPE", "texto_cru": "ok"},
    ])
    _patch_bronze(monkeypatch, bronze)

    docs = adapter.to_documents("18064")
    assert len(docs) == 1


# =============================================================================
# Not found / empty bronze
# =============================================================================

def test_to_documents_missing_id_returns_empty(tmp_path: Path, monkeypatch, adapter):
    bronze = _make_bronze(tmp_path, [
        {"url": "https://fapesp.br/18064", "titulo": "Outro", "texto_cru": "ok"},
    ])
    _patch_bronze(monkeypatch, bronze)
    assert adapter.to_documents("99999") == []


def test_to_documents_no_bronze_dir(tmp_path: Path, monkeypatch, adapter):
    _patch_bronze(monkeypatch, tmp_path)  # dir não existe
    assert adapter.to_documents("18064") == []


def test_to_documents_empty_texto_cru_returns_empty(tmp_path: Path, monkeypatch, adapter):
    bronze = _make_bronze(tmp_path, [
        {"url": "https://fapesp.br/18064", "titulo": "Foo", "texto_cru": "   "},
    ])
    _patch_bronze(monkeypatch, bronze)
    assert adapter.to_documents("18064") == []


# =============================================================================
# Dedup intra-arquivo (URL duplicada — gotcha §8 wikis/fapesp.md)
# =============================================================================

def test_to_documents_dedup_picks_first_occurrence(tmp_path: Path, monkeypatch, adapter):
    bronze = _make_bronze(tmp_path, [
        {"url": "https://fapesp.br/18064", "titulo": "PRIMEIRO", "texto_cru": "conteudo-primeiro"},
        {"url": "https://fapesp.br/18064", "titulo": "DUPLICATA", "texto_cru": "conteudo-duplicata"},
    ])
    _patch_bronze(monkeypatch, bronze)
    docs = adapter.to_documents("18064")
    assert "conteudo-primeiro" in docs[0]["units"][0]
    assert "conteudo-duplicata" not in docs[0]["units"][0]


# =============================================================================
# Bronze com múltiplos snapshots — lê o mais recente (sort por nome)
# =============================================================================

def test_to_documents_uses_latest_snapshot(tmp_path: Path, monkeypatch, adapter):
    raw_dir = tmp_path / "fapesp_raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "fapesp_scan_20260101_000000.json").write_text(
        json.dumps([{"url": "https://fapesp.br/18064", "titulo": "old",
                     "texto_cru": "texto-velho"}]),
        encoding="utf-8",
    )
    (raw_dir / "fapesp_scan_20260529_000000.json").write_text(
        json.dumps([{"url": "https://fapesp.br/18064", "titulo": "novo",
                     "texto_cru": "texto-novo"}]),
        encoding="utf-8",
    )
    _patch_bronze(monkeypatch, tmp_path)
    docs = adapter.to_documents("18064")
    assert "texto-novo" in docs[0]["units"][0]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
