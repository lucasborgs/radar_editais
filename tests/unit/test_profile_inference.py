"""Testes de `infer_financiamento` + wire no serializer de extract.

Cobre a Decisão 1: inferência
determinística de `tipos_financiamento_interesse` (PROPOSTA, eval-gated).
"""

from __future__ import annotations

import pytest

from radar.core.ingestion.profile_inference import (
    CAPITAL_RISCO,
    PESQUISA_COLABORATIVA,
    SUBVENCAO,
    infer_financiamento,
)
from radar.domain.user_profile import CompanyProfile

pytestmark = pytest.mark.unit

# (descrição, kwargs do perfil, esperado)
_CASES = [
    (
        "empresa de tech com TRL → subvenção",
        {"tipo_entidade": "empresa", "trl": 7, "one_liner": "Plataforma de tecnologia"},
        [SUBVENCAO],
    ),
    (
        "ICT → pesquisa colaborativa",
        {"tipo_entidade": "ICT", "descricao_atividades": "Instituto de pesquisa"},
        [PESQUISA_COLABORATIVA],
    ),
    (
        "universidade → pesquisa colaborativa",
        {"tipo_entidade": "universidade", "one_liner": "Laboratório de inovação"},
        [PESQUISA_COLABORATIVA],
    ),
    (
        "startup TRL baixo (≤4) → subvenção + pesquisa + capital risco",
        {"tipo_entidade": "startup", "trl": 3},
        [SUBVENCAO, PESQUISA_COLABORATIVA, CAPITAL_RISCO],
    ),
    (
        "empresa sem nenhum sinal técnico → []",
        {"tipo_entidade": "empresa", "one_liner": "Padaria de bairro", "trl": None},
        [],
    ),
    (
        "tipo desconhecido com sinal técnico → fallback subvenção",
        {"tipo_entidade": "cooperativa", "one_liner": "Desenvolvimento de software"},
        [SUBVENCAO],
    ),
    (
        "case-insensitivity: 'ict' minúsculo → pesquisa colaborativa",
        {"tipo_entidade": "ict", "descricao_atividades": "pesquisa aplicada"},
        [PESQUISA_COLABORATIVA],
    ),
    (
        "sinal técnico só por keyword acentuada (inovação) em empresa → subvenção",
        {"tipo_entidade": "empresa", "solution_summary": "Foco em inovação"},
        [SUBVENCAO],
    ),
    (
        "startup sem TRL mas entidade tech → subvenção + capital risco",
        {"tipo_entidade": "startup"},
        [SUBVENCAO, CAPITAL_RISCO],
    ),
]


@pytest.mark.parametrize(
    "desc,kwargs,esperado",
    _CASES,
    ids=[c[0] for c in _CASES],
)
def test_infer_financiamento(desc: str, kwargs: dict, esperado: list[str]):
    profile = CompanyProfile(**kwargs)
    assert infer_financiamento(profile) == esperado


def test_dedup_preserva_ordem():
    # universidade com TRL baixo bateria pesquisa_colaborativa por dois caminhos.
    profile = CompanyProfile(tipo_entidade="universidade", trl=2)
    assert infer_financiamento(profile) == [PESQUISA_COLABORATIVA]


def test_tipo_entidade_vazio_sem_sinal():
    # tipo_entidade default é "empresa" no dataclass; aqui forçamos vazio + sem tech.
    profile = CompanyProfile(tipo_entidade="", one_liner="Comércio varejista")
    assert infer_financiamento(profile) == []


# ── Wire no serializer: não sobrescreve valor já preenchido ──────────────────


def _make_extract_result(profile: CompanyProfile):
    from radar.core.ingestion.profile_extractor import ExtractResult

    return ExtractResult(
        profile=profile,
        confidence={},
        source_title="t",
        low_confidence=False,
        error=None,
    )


def test_serializer_infere_quando_vazio():
    from radar.api.routers.profile import _serialize_extract_result

    profile = CompanyProfile(tipo_entidade="empresa", trl=6, one_liner="tecnologia")
    out = _serialize_extract_result(_make_extract_result(profile))
    assert out["profile"]["tipos_financiamento_interesse"] == [SUBVENCAO]


def test_serializer_nao_sobrescreve_valor_existente():
    from radar.api.routers.profile import _serialize_extract_result

    # Perfil já tem mecanismo (ex.: edição manual) → inferência NÃO roda.
    profile = CompanyProfile(
        tipo_entidade="empresa",
        trl=6,
        one_liner="tecnologia",
        tipos_financiamento_interesse=[PESQUISA_COLABORATIVA],
    )
    out = _serialize_extract_result(_make_extract_result(profile))
    assert out["profile"]["tipos_financiamento_interesse"] == [PESQUISA_COLABORATIVA]
