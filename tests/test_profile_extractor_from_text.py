"""Testes do path extract_from_text de ProfileExtractor.

Cobre extração de CompanyProfile a partir de texto arbitrário (ex.: proposta
antiga), com _call_llm MOCKADO (nenhuma chamada real a LLM/rede):
  - texto válido → profile + confidence populados; low_confidence conforme regra
  - texto vazio/branco → erro controlado, sem exceção
  - _call_llm retorna None → required fields "missing" + erro

Também testa o serializer _serialize_extract_result de backend/routers/profile.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingestion.profile_extractor import ProfileExtractor  # noqa: E402

# ============================================================================
# Fixtures de texto
# ============================================================================

_PROPOSAL_TEXT = """Proposta de financiamento — ACME Bio Ltda.
A ACME Bio é uma startup de bioeconomia sediada em São Paulo que desenvolve
enzimas industriais para a indústria de papel e celulose."""

_FULL_LLM = {
    "nome": "ACME Bio Ltda.",
    "tipo_entidade": "startup",
    "one_liner": "Enzimas industriais para papel e celulose.",
    "solution_summary": "Plataforma enzimática.",
    "descricao_atividades": "Desenvolve e comercializa enzimas industriais.",
    "tamanho_empresa": "ME",
    "trl": 6,
    "uf": "sp",
    "ano_fundacao": 2018,
}


# ============================================================================
# extract_from_text — happy path
# ============================================================================

def test_extract_from_text_populates_profile_and_confidence(monkeypatch):
    extractor = ProfileExtractor()
    monkeypatch.setattr(extractor, "_call_llm", lambda text: dict(_FULL_LLM))

    result = extractor.extract_from_text(_PROPOSAL_TEXT, source_title="proposta.pdf")

    assert result.error is None
    assert result.source_title == "proposta.pdf"
    assert result.profile.nome == "ACME Bio Ltda."
    assert result.profile.tipo_entidade == "startup"
    assert result.profile.uf == "SP"  # normalizado para upper
    # 4 campos obrigatórios extraídos → low_confidence False (>= 2 high)
    assert result.confidence["nome"] == "high"
    assert result.confidence["one_liner"] == "high"
    assert result.low_confidence is False


def test_extract_from_text_low_confidence_when_few_required(monkeypatch):
    extractor = ProfileExtractor()
    # Apenas 1 campo obrigatório preenchido → required_found < 2 → low_confidence
    monkeypatch.setattr(
        extractor, "_call_llm", lambda text: {"nome": "ACME"},
    )

    result = extractor.extract_from_text("texto qualquer", source_title="x")

    assert result.error is None
    assert result.confidence["nome"] == "high"
    assert result.low_confidence is True


# ============================================================================
# extract_from_text — texto vazio
# ============================================================================

def test_extract_from_text_blank_returns_controlled_error(monkeypatch):
    extractor = ProfileExtractor()
    # _call_llm não deve nem ser chamado para texto vazio
    monkeypatch.setattr(
        extractor, "_call_llm",
        lambda text: (_ for _ in ()).throw(AssertionError("não deve chamar LLM")),
    )

    for blank in ("", "   ", "\n\t "):
        result = extractor.extract_from_text(blank)
        assert result.error is not None
        assert result.low_confidence is True
        assert all(v == "missing" for v in result.confidence.values())


# ============================================================================
# extract_from_text — LLM indisponível
# ============================================================================

def test_extract_from_text_llm_none_marks_missing(monkeypatch):
    extractor = ProfileExtractor()
    monkeypatch.setattr(extractor, "_call_llm", lambda text: None)

    result = extractor.extract_from_text(_PROPOSAL_TEXT, source_title="p.pdf")

    assert result.error == "llm_unavailable"
    assert result.low_confidence is True
    for field in ("nome", "tipo_entidade", "one_liner", "descricao_atividades"):
        assert result.confidence[field] == "missing"


# ============================================================================
# Serializer (_serialize_extract_result) — backend/routers/profile.py
# ============================================================================

def test_serialize_extract_result_shape(monkeypatch):
    from backend.routers.profile import _serialize_extract_result

    extractor = ProfileExtractor()
    monkeypatch.setattr(extractor, "_call_llm", lambda text: dict(_FULL_LLM))
    result = extractor.extract_from_text(_PROPOSAL_TEXT, source_title="proposta.pdf")

    payload = _serialize_extract_result(result)

    assert set(payload.keys()) == {
        "profile", "confidence", "source_title", "low_confidence", "error",
    }
    assert payload["source_title"] == "proposta.pdf"
    assert payload["low_confidence"] is False
    assert payload["error"] is None
    # campos do perfil presentes no dict serializado
    for field in (
        "nome", "cnpj", "tipo_entidade", "one_liner", "solution_summary",
        "descricao_atividades", "portfolio_projetos", "tamanho_empresa",
        "capital_social", "trl", "equipe_resumo", "tipos_financiamento_interesse",
    ):
        assert field in payload["profile"]
    assert payload["profile"]["nome"] == "ACME Bio Ltda."
