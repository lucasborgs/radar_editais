"""Fixtures compartilhadas da suíte.

Os testes dos agentes (writing/explore/profile) monkeypatcham `run_agent`,
mas o call site agora resolve o provider via `resolve_agent_provider`, que
exige ao menos uma API key no ambiente (resiliência multi-provider — ver
core/agent_runtime.py). Como a CI/dev nem sempre exporta credenciais reais,
garantimos chaves-dummy quando ausentes. Isso é inócuo: nos testes o
`run_agent` real nunca é chamado (está mockado), então as chaves não saem
do processo.

Testes que precisam validar a ausência de chave (ex.: resolve_agent_provider
sem credencial) sobrescrevem localmente com monkeypatch.delenv — como o
fixture usa o mesmo objeto monkeypatch, a precedência fica determinística.
"""
from __future__ import annotations

import os

import pytest

# Telemetria Langfuse OFF na suíte (nível de módulo: roda quando o pytest importa
# este conftest, ANTES dos test modules e seus `load_dotenv()`). Forçamos as keys
# a "" — `load_dotenv(override=False)` não sobrescreve var já presente, e
# `core.telemetry._is_configured()` lê `bool("")` = False → cliente não inicializa.
# Sem isso, as keys reais do .env vazariam pro processo de teste, o exporter OTel
# do Langfuse tentaria flush no shutdown e cuspia um 401 (ruído na suíte).
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""


@pytest.fixture(autouse=True)
def _ensure_llm_keys(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if not os.environ.get(var):
            monkeypatch.setenv(var, f"test-dummy-{var.lower()}")
    yield
