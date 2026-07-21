"""
Testes do helper de identidade cross-source (core/edital_id.py).

Cobre make_id, parse_id, source_of, native_id_of e o roundtrip do contrato.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from core.kg.edital_id import (  # noqa: E402
    make_id,
    native_id_of,
    parse_id,
    source_of,
)

pytestmark = pytest.mark.unit

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
