"""Gate de perfil da WritingSession (Fase 2 — agentic-evolution).

Cobre:
  • CompanyProfile.is_complete_for_writing() — campos mínimos + ordem do missing.
  • WritingSession.__init__ barra a CRIAÇÃO com perfil incompleto ANTES de tocar
    o DB (não cria sessão órfã), levantando ProfileIncompleteError.

Não bate em DB/LLM: o caso incompleto curto-circuita no gate, antes de
_create_in_db. O caso completo é coberto pela checagem pura do domínio.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.services.writing_session import (  # noqa: E402
    ProfileIncompleteError,
    WritingSession,
)
from domain.user_profile import CompanyProfile  # noqa: E402


def _complete_kwargs() -> dict:
    return dict(
        nome="ACME Bio",
        tipo_entidade="startup",
        trl=5,
        tamanho_empresa="ME",
        uf="SP",
        descricao_atividades="Bioinsumos para agricultura regenerativa.",
    )


def test_complete_profile_passes():
    ok, missing = CompanyProfile(**_complete_kwargs()).is_complete_for_writing()
    assert ok is True
    assert missing == []


def test_trl_zero_is_present():
    # trl conta como presente quando is not None (truthiness não basta: 0 é None-ish).
    kw = _complete_kwargs() | {"trl": 0}
    ok, missing = CompanyProfile(**kw).is_complete_for_writing()
    assert ok is True
    assert "trl" not in missing


@pytest.mark.parametrize("field", ["nome", "trl", "tamanho_empresa", "uf", "descricao_atividades"])
def test_each_missing_field_is_reported(field):
    kw = _complete_kwargs()
    kw[field] = None if field == "trl" else ""
    ok, missing = CompanyProfile(**kw).is_complete_for_writing()
    assert ok is False
    assert missing == [field]


def test_missing_preserves_canonical_order():
    # Esvazia tudo: missing deve sair na ordem de _WRITING_MIN_FIELDS.
    # tipo_entidade tem default "empresa"; força vazio para entrar no missing.
    p = CompanyProfile(
        nome="", tipo_entidade="", trl=None, tamanho_empresa="", uf="",
        descricao_atividades="",
    )
    ok, missing = p.is_complete_for_writing()
    assert ok is False
    assert missing == list(CompanyProfile._WRITING_MIN_FIELDS)


def test_writing_session_gate_blocks_before_db():
    """Perfil incompleto → ProfileIncompleteError e NENHUM acesso ao DB."""
    db = MagicMock()
    incomplete = CompanyProfile(nome="ACME Bio")  # falta trl/tamanho/uf/descricao

    with pytest.raises(ProfileIncompleteError) as exc:
        WritingSession(
            db=db,
            workspace_id="ws_1",
            profile=incomplete,
            edital_id="finep:774",
        )

    assert "trl" in exc.value.missing_fields
    assert "uf" in exc.value.missing_fields
    # Gate roda antes de _create_in_db: nenhuma query foi disparada.
    db.table.assert_not_called()
