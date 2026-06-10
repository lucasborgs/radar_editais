"""
Testes do filtro determinístico PME (core/pme_filter.py).

Cobre os 3 sinais (programa, público, exclusor), precedência accept-sobre-reject,
e fixtures reais observadas no spike FAPESP de 2026-05-29.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from core.kg import wiki_schema  # noqa: E402
from core.pme_filter import (  # noqa: E402
    is_target_relevant,
    relevance_with_reason,
)

# Garante leitura limpa do doc autoritativo a cada execução de teste
wiki_schema.clear_cache()


# =============================================================================
# FIXTURES — metadata reais ou plausíveis de chamadas
# =============================================================================

PIPE_AGRO = {
    "titulo": "Chamada de Propostas para o Programa PIPE Jornada Tecnológica Agro – Fase 1",
    "modalidade": "PIPE - Fase 1",
    "categoria": "Programas FAPESP",
    "publico_alvo": ["Empresas"],
}

PIPE_SOBERANIA = {
    "titulo": "PIPE Jornada Tecnológica Soberania Digital – Fase 1",
    "modalidade": "PIPE - Fase 1",
    "publico_alvo": ["Empresas"],
}

# Chamada PIPE que menciona "bolsa" no resumo — testa precedência accept>reject
PIPE_COM_BOLSA_NO_TEXTO = {
    "titulo": "PIPE Saúde Fase 1",
    "modalidade": "PIPE",
    "descricao_resumo": "Inclui Bolsa de Pesquisa Pequena Empresa para o pesquisador responsável",
    "publico_alvo": ["Empresas"],
}

AUXILIO_INOVACAO = {
    "titulo": "Chamada de Propostas - Auxílio à Inovação Regular",
    "modalidade": "Auxílio à Inovação Regular",
    "publico_alvo": [],
}

FINEP_SUBVENCAO = {
    "titulo": "FINEP — Subvenção Econômica Indústria 4.0",
    "modalidade": "Subvenção Econômica",
    "publico_alvo": ["Empresas"],
}

BNDES_FUNTEC = {
    "titulo": "BNDES Funtec — Bioeconomia Amazônica",
    "programa": "Funtec",
    "publico_alvo": ["ICTs", "Empresas"],
}

CENTELHA_FAPESC = {
    "titulo": "Edital Centelha 3 — Apoio a Negócios Inovadores",
    "programa": "Programa Centelha",
    "publico_alvo": ["Empresas"],
}

MOVER_FUNDEP = {
    "titulo": "Chamada MOVER — Cadeia Eletrificada",
    "programa": "MOVER",
    "publico_alvo": ["Empresas"],
}

# Caso onde só o público sinaliza — sem alias de programa visível no texto
PUBLICO_EMPRESA_SEM_PROGRAMA = {
    "titulo": "Chamada de fluxo contínuo para desenvolvimento tecnológico",
    "publico_alvo": ["Empresas"],
}

# Casos acadêmicos puros — devem rejeitar
BOLSA_DOUTORADO = {
    "titulo": "Bolsa de Doutorado - FAPESP",
    "modalidade": "Bolsa",
    "publico_alvo": ["Pesquisadores"],
}

AUXILIO_PESQUISA_REGULAR = {
    "titulo": "Auxílio à Pesquisa Regular",
    "modalidade": "Auxílio à Pesquisa Regular",
    "publico_alvo": ["Pesquisadores"],
}

PROJETO_TEMATICO = {
    "titulo": "Auxílio à Pesquisa - Projeto Temático",
    "modalidade": "Projeto Temático",
    "publico_alvo": ["Pesquisadores"],
}

ESPCA = {
    "titulo": "Escolas São Paulo de Ciência Avançada (ESPCA) - 20ª Chamada",
    "modalidade": "ESPCA",
    "publico_alvo": [],
}

PROPASP = {
    "titulo": "PROPASP 2026 — FAPESP/DAAD",
    "modalidade": "Auxílio à Pesquisa Regular",
    "publico_alvo": ["Pesquisadores"],
}

JOVEM_PESQUISADOR = {
    "titulo": "Auxílio Jovem Pesquisador - Fase 2",
    "modalidade": "Auxílio Jovem Pesquisador",
    "publico_alvo": ["Pesquisadores"],
}

# Caso ambíguo — nada bate
EDITAL_AMBIGUO = {
    "titulo": "Apoio à formação de pesquisadores em ambiente corporativo",
    "publico_alvo": [],
}

# Caso degenerado — metadata vazio
METADATA_VAZIO: dict = {}

# Caso com publico_alvo None
METADATA_PUBLICO_NONE = {
    "titulo": "Chamada qualquer",
    "publico_alvo": None,
}


# =============================================================================
# TESTES — accept por programa-whitelist
# =============================================================================

@pytest.mark.parametrize("metadata,expected_alias", [
    (PIPE_AGRO, "pipe"),
    (PIPE_SOBERANIA, "pipe"),
    (AUXILIO_INOVACAO, "inovacao"),
    (FINEP_SUBVENCAO, "subvencao"),
    (BNDES_FUNTEC, "funtec"),
    (CENTELHA_FAPESC, "centelha"),
    (MOVER_FUNDEP, "mover"),
])
def test_accept_by_program_alias(metadata: dict, expected_alias: str):
    decision, reason = relevance_with_reason(metadata)
    assert decision == "accept", f"esperado accept, veio {decision} ({reason})"
    assert expected_alias in reason, f"alias {expected_alias!r} não aparece em {reason!r}"


# =============================================================================
# TESTES — accept por público-whitelist
# =============================================================================

def test_accept_by_publico_empresa():
    decision, reason = relevance_with_reason(PUBLICO_EMPRESA_SEM_PROGRAMA)
    assert decision == "accept"
    assert reason.startswith("publico:")
    assert "Empresas" in reason


# =============================================================================
# TESTES — precedência accept-sobre-reject
# =============================================================================

def test_pipe_with_bolsa_in_text_still_accepts():
    """PIPE Fase 1 menciona 'Bolsa de Pesquisa Pequena Empresa' — precedência
    de programa deve vencer o exclusor 'bolsa'."""
    decision, reason = relevance_with_reason(PIPE_COM_BOLSA_NO_TEXTO)
    assert decision == "accept", f"precedência falhou: {decision} ({reason})"
    assert reason.startswith("programa:pipe")


# =============================================================================
# TESTES — reject por exclusor acadêmico
# =============================================================================

@pytest.mark.parametrize("metadata", [
    BOLSA_DOUTORADO,
    AUXILIO_PESQUISA_REGULAR,
    PROJETO_TEMATICO,
    ESPCA,
    PROPASP,
    JOVEM_PESQUISADOR,
])
def test_reject_by_academic_excluder(metadata: dict):
    decision, reason = relevance_with_reason(metadata)
    assert decision == "reject", f"esperado reject, veio {decision} ({reason})"
    assert reason.startswith("exclusor:"), f"reason inesperado: {reason}"


# =============================================================================
# TESTES — unclear quando nenhum sinal dispara
# =============================================================================

def test_unclear_when_no_signal():
    decision, reason = relevance_with_reason(EDITAL_AMBIGUO)
    assert decision == "unclear"
    assert reason == "sem-sinal"


def test_empty_metadata_is_unclear():
    """Edital vazio não deve crashar — vai pra unclear."""
    decision, _ = relevance_with_reason(METADATA_VAZIO)
    assert decision == "unclear"


def test_publico_none_does_not_crash():
    """publico_alvo=None é caso real (campo opcional) — não pode quebrar."""
    decision, _ = relevance_with_reason(METADATA_PUBLICO_NONE)
    assert decision == "unclear"


# =============================================================================
# TESTES — is_target_relevant retorna mesma decisão que relevance_with_reason
# =============================================================================

@pytest.mark.parametrize("metadata", [
    PIPE_AGRO, AUXILIO_INOVACAO, BOLSA_DOUTORADO, EDITAL_AMBIGUO, METADATA_VAZIO,
])
def test_is_target_relevant_matches_with_reason(metadata: dict):
    decision_short = is_target_relevant(metadata)
    decision_full, _ = relevance_with_reason(metadata)
    assert decision_short == decision_full


# =============================================================================
# RUNNER (compat com test_wiki_schema_consistency.py)
# =============================================================================

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
