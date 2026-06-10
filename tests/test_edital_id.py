"""
Testes do helper de identidade cross-source (core/edital_id.py).

Cobre make_id, parse_id, source_of, native_id_of, wiki_page_path, iter_wiki_pages.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from config import KG_WIKI_DIR  # noqa: E402
from core.kg.edital_id import (  # noqa: E402
    iter_wiki_pages,
    make_id,
    native_id_of,
    parse_id,
    source_of,
    wiki_page_path,
)

# =============================================================================
# make_id
# =============================================================================

@pytest.mark.parametrize("source,native,expected", [
    ("finep", "782", "finep:782"),
    ("fapesp", 18064, "fapesp:18064"),       # int aceito por conveniência
    ("bndes", "funtec-2026", "bndes:funtec-2026"),  # slug com hífen
])
def test_make_id(source: str, native, expected: str):
    assert make_id(source, native) == expected


@pytest.mark.parametrize("bad_source", ["", "fi:nep", "with:colon"])
def test_make_id_rejects_bad_source(bad_source: str):
    with pytest.raises(ValueError):
        make_id(bad_source, "782")


# =============================================================================
# parse_id / source_of / native_id_of
# =============================================================================

@pytest.mark.parametrize("eid,expected_source,expected_native", [
    ("finep:782", "finep", "782"),
    ("fapesp:18064", "fapesp", "18064"),
    ("bndes:funtec-2026", "bndes", "funtec-2026"),
    # native_id pode conter ':' adicional — split com maxsplit=1 preserva
    ("fapesp:18064:fase-1", "fapesp", "18064:fase-1"),
])
def test_parse_id(eid: str, expected_source: str, expected_native: str):
    source, native = parse_id(eid)
    assert source == expected_source
    assert native == expected_native
    assert source_of(eid) == expected_source
    assert native_id_of(eid) == expected_native


@pytest.mark.parametrize("bad_eid", ["", "782", "no-colon-here"])
def test_parse_id_rejects_unprefixed(bad_eid: str):
    """Hard cut pós-Épico B: id sem prefixo é erro explícito, não fallback."""
    with pytest.raises(ValueError, match="sem prefixo"):
        parse_id(bad_eid)


# =============================================================================
# wiki_page_path
# =============================================================================

def test_wiki_page_path_uses_source_subfolder():
    path = wiki_page_path("finep:782")
    assert path == KG_WIKI_DIR / "finep" / "782.json"


def test_wiki_page_path_for_alternative_source():
    path = wiki_page_path("fapesp:18064")
    assert path == KG_WIKI_DIR / "fapesp" / "18064.json"


def test_wiki_page_path_does_not_check_existence(tmp_path: Path, monkeypatch):
    """wiki_page_path é função pura — não toca disco, só monta o path."""
    # Independente de existência, retorna o path esperado.
    p = wiki_page_path("finep:nao-existe")
    assert p.name == "nao-existe.json"
    assert p.parent.name == "finep"


# =============================================================================
# iter_wiki_pages
# =============================================================================

def test_iter_wiki_pages_ignores_dotfiles(tmp_path: Path, monkeypatch):
    """iter_wiki_pages NÃO retorna .etl_process_cache.json e similares."""
    fake_kg = tmp_path / "wiki"
    (fake_kg / "finep").mkdir(parents=True)
    (fake_kg / "finep" / "782.json").write_text("{}", encoding="utf-8")
    (fake_kg / "finep" / "601.json").write_text("{}", encoding="utf-8")
    (fake_kg / ".etl_process_cache.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("core.kg.edital_id.KG_WIKI_DIR", fake_kg)

    paths = iter_wiki_pages()
    names = sorted(p.name for p in paths)
    assert names == ["601.json", "782.json"]


def test_iter_wiki_pages_filter_by_source(tmp_path: Path, monkeypatch):
    fake_kg = tmp_path / "wiki"
    (fake_kg / "finep").mkdir(parents=True)
    (fake_kg / "fapesp").mkdir(parents=True)
    (fake_kg / "finep" / "782.json").write_text("{}", encoding="utf-8")
    (fake_kg / "fapesp" / "18064.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("core.kg.edital_id.KG_WIKI_DIR", fake_kg)

    assert len(iter_wiki_pages()) == 2
    assert len(iter_wiki_pages("finep")) == 1
    assert len(iter_wiki_pages("fapesp")) == 1
    assert len(iter_wiki_pages("bndes")) == 0  # source ausente → []


def test_iter_wiki_pages_returns_empty_when_dir_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.kg.edital_id.KG_WIKI_DIR", tmp_path / "does-not-exist")
    assert iter_wiki_pages() == []


# =============================================================================
# Roundtrip
# =============================================================================

@pytest.mark.parametrize("source,native", [
    ("finep", "782"),
    ("fapesp", "18064"),
    ("bndes", "funtec-2026"),
])
def test_roundtrip(source: str, native: str):
    """make_id ↔ parse_id é inverso."""
    eid = make_id(source, native)
    s, n = parse_id(eid)
    assert (s, n) == (source, native)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
