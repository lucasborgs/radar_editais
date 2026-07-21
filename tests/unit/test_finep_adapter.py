
from pathlib import Path

import pytest

from radar.pipeline.adapters.finep import (
    _filter_to_latest_versions,
    _version_info,
    _versioned_documents,
)

pytestmark = pytest.mark.unit


def test_regulamentos_reais_formam_uma_familia_versionada():
    base = "07_08_2024_Regulamento_Conhecimento_Brasil"
    first = "Regulamento_Conhecimento_Brasil_Rerratificado_07_02_2025"
    third = "Regulamento_Conhecimento_Brasil_3_Rerratificacao_09_02_2026"
    assert _version_info(base)[0] == "__regulamento__"
    assert _version_info(first)[0] == "__regulamento__"
    assert _version_info(third)[0] == "__regulamento__"
    assert _version_info(third)[1] > _version_info(first)[1] > _version_info(base)[1]


def test_apenas_terceira_rerratificacao_fica_ativa():
    paths = [
        Path("07_08_2024_Regulamento_Conhecimento_Brasil.pdf"),
        Path("Regulamento_Conhecimento_Brasil_Rerratificado_07_02_2025.pdf"),
        Path("Regulamento_Conhecimento_Brasil_3_Rerratificacao_09_02_2026.pdf"),
    ]
    assert _filter_to_latest_versions(paths) == [paths[2]]
    classified = dict(_versioned_documents(paths))
    assert classified[paths[0]]["authority_state"] == "superseded"
    assert classified[paths[1]]["authority_state"] == "superseded"
    assert classified[paths[2]]["authority_state"] == "vigente"
    assert classified[paths[2]]["published_at"] == "2026-02-09"
    assert classified[paths[2]]["revision"] == 3


def test_anexo_rerratificado_tambem_e_versionado():
    base = Path("Anexo_1_-_Características_Específicas_da_Seleção_Pública.pdf")
    second = Path("Anexo_1_rerratificado_-_fevereiro_2025.pdf")
    third = Path("3ª_Rerratificação_do_Anexo_1.pdf")
    classified = dict(_versioned_documents([base, second, third]))
    assert classified[base]["authority_state"] == "superseded"
    assert classified[second]["authority_state"] == "superseded"
    assert classified[third]["authority_state"] == "vigente"
    assert classified[third]["published_at"] == "2026-02-09"
