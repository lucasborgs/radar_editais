"""Testes do Contextual Retrieval (contexto-no-chunk antes do embed)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import contextual_retrieval as cr  # noqa: E402


def test_desligado_retorna_corpo_cru(monkeypatch):
    monkeypatch.setenv("CONTEXTUAL_RETRIEVAL", "false")
    chunks = [{"text": "corpo um"}, {"text": "corpo dois"}]
    assert cr.contextualize_chunks(chunks) == ["corpo um", "corpo dois"]


def test_sem_api_key_degrada(monkeypatch):
    monkeypatch.setenv("CONTEXTUAL_RETRIEVAL", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cr.contextualize_chunks([{"text": "x"}]) == ["x"]


def test_ligado_prepende_contexto(monkeypatch):
    monkeypatch.setenv("CONTEXTUAL_RETRIEVAL", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    fake.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Seção 1, edital X."))]
    )
    with patch("core.llm.llm_client.make_client", return_value=fake):
        out = cr.contextualize_chunks([{"text": "elegibilidade: empresas"}])
    assert out == ["Seção 1, edital X.\n\nelegibilidade: empresas"]


def test_falha_por_chunk_cai_para_cru(monkeypatch):
    monkeypatch.setenv("CONTEXTUAL_RETRIEVAL", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    fake.chat.completions.create.side_effect = RuntimeError("boom")
    with patch("core.llm.llm_client.make_client", return_value=fake):
        out = cr.contextualize_chunks([{"text": "corpo"}])
    assert out == ["corpo"]  # nunca quebra
