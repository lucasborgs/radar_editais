"""Lifecycle de produção da projeção da Fase 1 (KG-P1B-2).

Cobre o contrato do refresh automático pós-gold + os sinais estruturais da
suíte `explore`:

  1. flag default off e sem conexão (outcome `disabled`, build NUNCA chamado);
  2. outcome `built` com retorno sanitizado (só trigger/outcome/duração/
     generation/contagens int — `source_hash` e demais chaves ficam fora);
  3. idempotência: gold inalterado → outcome `skipped`;
  4. best-effort: falha do build → outcome `failed` (categoria+tipo, NUNCA a
     mensagem) e o refresh NUNCA levanta;
  5. sem imports proibidos / sem vazamento de mensagem no módulo;
  6. acumulador estrutural das graph tools (`run_stats`/`reset_run_stats`);
  7. diagnósticos do eval `explore`: graph_tool_usage, graph_fallback_rate,
     graph_latency_ms e preservação do tool_contract/answer_contract.

Correção da auditoria KG-P1B-2 (aditiva):
  8. `response_latency_ms`: métrica por caso da resposta conectada INTEIRA,
     hermético → None, presente com grafo off e on, comentário fixo sem
     conteúdo da pergunta/resposta;
  9. `tool_contract` NÃO enfraquecido: `graph_community` sozinha não satisfaz
     rotas factuais; só `graph_explore`/`graph_reason` (factuais) somam às
     tools legadas; `graph_community` pode coexistir com uma tool factual.

Hermético: nenhum teste toca banco/LLM — `ingest.build` e `store` são
monkeypatched.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from radar.core.kg.phase1 import lifecycle, tools
from radar.core.kg.phase1.lifecycle import AUTO_REFRESH_FLAG

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# 1. Flag default off
# ─────────────────────────────────────────────────────────────────────────────

def test_auto_refresh_enabled_default_off(monkeypatch):
    monkeypatch.delenv(AUTO_REFRESH_FLAG, raising=False)
    assert lifecycle.auto_refresh_enabled() is False
    monkeypatch.setenv(AUTO_REFRESH_FLAG, "false")
    assert lifecycle.auto_refresh_enabled() is False
    monkeypatch.setenv(AUTO_REFRESH_FLAG, "0")
    assert lifecycle.auto_refresh_enabled() is False


def test_auto_refresh_enabled_true_variants(monkeypatch):
    for value in ("true", "TRUE", "True"):
        monkeypatch.setenv(AUTO_REFRESH_FLAG, value)
        assert lifecycle.auto_refresh_enabled() is True


def test_refresh_disabled_never_calls_build(monkeypatch):
    monkeypatch.delenv(AUTO_REFRESH_FLAG, raising=False)
    from radar.core.kg.phase1 import ingest

    monkeypatch.setattr(
        ingest, "build",
        lambda **k: (_ for _ in ()).throw(AssertionError("não deve chamar build")),
    )

    out = lifecycle.refresh_after_gold(trigger="daily_etl")

    assert out == {"trigger": "daily_etl", "outcome": "disabled"}


# ─────────────────────────────────────────────────────────────────────────────
# 2 + 3. built/skipped com retorno sanitizado
# ─────────────────────────────────────────────────────────────────────────────

def _enable(monkeypatch):
    monkeypatch.setenv(AUTO_REFRESH_FLAG, "true")


def test_refresh_built_outcome_is_sanitized(monkeypatch):
    _enable(monkeypatch)
    from radar.core.kg.phase1 import ingest

    monkeypatch.setattr(ingest, "build", lambda **k: {
        "skipped": False, "generation": 7, "source_hash": "md5:conteudo",
        "nodes": 3, "edges": 4, "quality_nodes": 2, "communities": 1,
        "similar_a": 1, "potencial_parceria": 1,
    })

    out = lifecycle.refresh_after_gold(trigger="promoted_edital")

    assert out["trigger"] == "promoted_edital"
    assert out["outcome"] == "built"
    assert out["generation"] == 7
    assert out["nodes"] == 3
    assert out["similar_a"] == 1
    assert isinstance(out["duration_ms"], int) and out["duration_ms"] >= 0
    # surface mínima: nada além do contrato vaza (em especial source_hash)
    assert "source_hash" not in out
    assert set(out) <= {
        "trigger", "outcome", "duration_ms", "generation",
        "nodes", "edges", "quality_nodes", "communities",
        "similar_a", "potencial_parceria",
    }


def test_refresh_skipped_when_gold_unchanged(monkeypatch):
    _enable(monkeypatch)
    from radar.core.kg.phase1 import ingest

    monkeypatch.setattr(
        ingest, "build",
        lambda **k: {"skipped": True, "generation": 5, "source_hash": "md5:igual"},
    )

    out = lifecycle.refresh_after_gold(trigger="daily_etl")

    assert out["outcome"] == "skipped"
    assert out["generation"] == 5
    assert "source_hash" not in out


# ─────────────────────────────────────────────────────────────────────────────
# 4. Best-effort: falha vira outcome `failed`, nunca levanta, mensagem não vaza
# ─────────────────────────────────────────────────────────────────────────────

def test_refresh_failed_is_sanitized_and_never_raises(monkeypatch):
    _enable(monkeypatch)
    from radar.core.kg.phase1 import ingest

    secret = "postgresql://user:password@secret-host:5432/db?application_name=leak"
    monkeypatch.setattr(
        ingest, "build",
        lambda **k: (_ for _ in ()).throw(RuntimeError(f"conexão recusada: {secret}")),
    )

    out = lifecycle.refresh_after_gold(trigger="daily_etl")

    assert out["outcome"] == "failed"
    assert out["error"] == {"category": "unexpected_error", "type": "RuntimeError"}
    assert isinstance(out["duration_ms"], int) and out["duration_ms"] >= 0
    assert secret not in json.dumps(out)


def test_refresh_contract_error_category(monkeypatch):
    _enable(monkeypatch)
    from radar.core.kg.phase1 import ingest

    monkeypatch.setattr(ingest, "build", lambda **k: (_ for _ in ()).throw(ValueError("x")))

    out = lifecycle.refresh_after_gold(trigger="daily_etl")

    assert out["outcome"] == "failed"
    assert out["error"]["category"] == "contract_error"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Módulo sem imports proibidos e sem uso da mensagem de exceção
# ─────────────────────────────────────────────────────────────────────────────

def test_lifecycle_no_forbidden_imports_and_no_message_usage():
    path = Path(lifecycle.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    forbidden = {
        "openai", "anthropic", "langchain", "requests", "httpx", "urllib",
        "aiohttp", "radar.core.llm", "radar.core.retrieval.embedder",
        "radar.core.kg.spike",
    }
    assert not (imports & forbidden), f"imports proibidos: {sorted(imports & forbidden)}"
    source = path.read_text(encoding="utf-8")
    assert "str(exc)" not in source, "a mensagem da exceção nunca é usada"
    assert "get_dsn" not in source, "DSN nunca passa por aqui"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Acumulador estrutural das graph tools (observabilidade do eval)
# ─────────────────────────────────────────────────────────────────────────────

def test_run_stats_accumulate_and_reset():
    tools.reset_run_stats()
    tools._observe("graph_explore", outcome="hit", generation_id=3,
                   duration_ms=10.0, n_nodes=2, fallback=False)
    tools._observe("graph_explore", outcome="unavailable", generation_id=None,
                   duration_ms=5.0, fallback=True)
    tools._observe("graph_reason", outcome="hit", generation_id=3,
                   duration_ms=7.5, fallback=False)

    stats = tools.run_stats()

    assert stats["graph_explore"] == {"calls": 2.0, "fallbacks": 1.0, "duration_ms": 15.0}
    assert stats["graph_reason"] == {"calls": 1.0, "fallbacks": 0.0, "duration_ms": 7.5}
    tools.reset_run_stats()
    assert tools.run_stats() == {}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Diagnósticos do eval `explore` (KG-P1B-2)
# ─────────────────────────────────────────────────────────────────────────────

def _eval_output(**overrides):
    base = {"route": "EDITAL_FACT", "answer": "resposta", "called_tools": []}
    base.update(overrides)
    return base


def test_graph_tool_usage_hermetic_returns_none(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_graph_tool_usage(output={"route": "EDITAL_FACT"}, expected_output={})
    assert out is None, "sem answer (hermético) → não pontua"


def test_graph_tool_usage_when_graph_called(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_graph_tool_usage(
        output=_eval_output(called_tools=["search_entities", "graph_explore"]),
        expected_output={},
    )
    assert out["name"] == "graph_tool_usage"
    assert out["value"] == 1.0


def test_graph_tool_usage_when_flag_off(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "false")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_graph_tool_usage(
        output=_eval_output(called_tools=["graph_explore"]),
        expected_output={},
    )
    assert out is None, "grafo desligado → diagnóstico não pontua"


def test_graph_tool_usage_when_not_called(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_graph_tool_usage(
        output=_eval_output(called_tools=["search_entities"]),
        expected_output={},
    )
    assert out["value"] == 0.0


def test_graph_fallback_rate_and_latency_aggregates():
    tools.reset_run_stats()
    from radar.core.eval import explore as explore_eval

    tools._observe("graph_explore", outcome="hit", generation_id=3, duration_ms=20.0)
    tools._observe("graph_explore", outcome="error", generation_id=None,
                   duration_ms=30.0, fallback=True)

    fallback = explore_eval.eval_graph_fallback_rate(item_results=[])
    latency = explore_eval.eval_graph_latency_ms(item_results=[])

    assert fallback["name"] == "graph_fallback_rate"
    assert fallback["value"] == 0.5
    assert latency["name"] == "graph_latency_ms"
    assert latency["value"] == 25.0


def test_graph_fallback_rate_none_without_calls():
    tools.reset_run_stats()
    from radar.core.eval import explore as explore_eval

    assert explore_eval.eval_graph_fallback_rate(item_results=[]) is None
    assert explore_eval.eval_graph_latency_ms(item_results=[]) is None


def test_eval_tool_contract_accepts_graph_tools():
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_tool_contract(
        output=_eval_output(called_tools=["graph_explore"]),
        expected_output={"route": "EDITAL_FACT"},
    )
    assert out["value"] == 1.0, "graph tools são aditivas e válidas nas rotas de fato"


def test_explore_suite_wires_diagnostics():
    from radar.core.eval import explore as explore_eval

    suite = explore_eval.SUITE
    names = {getattr(ev, "__name__", None) for ev in suite.evaluators}
    assert {"eval_route", "eval_tool_contract", "eval_answer_contract",
            "eval_graph_tool_usage", "eval_response_latency_ms"} <= names
    run_names = {getattr(rev, "__name__", None) for rev in suite.run_evaluators}
    assert run_names == {"eval_graph_fallback_rate", "eval_graph_latency_ms"}
    assert suite.version == "4"
    assert "KG_PHASE1_EXPLORE_ENABLED" in suite.manifest_env
    assert "KG_PHASE1_AUTO_REFRESH_ENABLED" in suite.manifest_env


# ─────────────────────────────────────────────────────────────────────────────
# 8. Correção da auditoria KG-P1B-2 — response_latency_ms
# ─────────────────────────────────────────────────────────────────────────────

def test_response_latency_hermetic_returns_none(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_response_latency_ms(
        output={"route": "EDITAL_FACT"}, expected_output={}
    )
    assert out is None, "sem answer (hermético) → não mede latência de resposta conectada"


def test_response_latency_connected_non_negative(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_response_latency_ms(
        output=_eval_output(response_latency_ms=12.345), expected_output={}
    )
    assert out["name"] == "response_latency_ms"
    assert out["value"] == 12.35
    assert out["value"] >= 0.0


def test_response_latency_exists_with_graph_off_and_on(monkeypatch):
    for flag in ("false", "true"):
        monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", flag)
        from radar.core.eval import explore as explore_eval

        out = explore_eval.eval_response_latency_ms(
            output=_eval_output(called_tools=["graph_explore"], response_latency_ms=3.0),
            expected_output={},
        )
        assert out is not None, f"grafo {flag} → métrica segue presente"
        assert out["value"] == 3.0


def test_response_latency_rejects_non_number(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    for bad in (True, "rápido", None):
        out = explore_eval.eval_response_latency_ms(
            output=_eval_output(response_latency_ms=bad), expected_output={}
        )
        assert out is None, f"valor não numérico {bad!r} → não pontua"


def test_response_latency_comment_is_fixed_and_content_free(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    pergunta_sigilosa = "qual o token de acesso do sistema interno?"
    resposta_sigilosa = "o token é SEU-SEGREDO-BRUTO-123"
    out = explore_eval.eval_response_latency_ms(
        output=_eval_output(
            answer=resposta_sigilosa,
            query=pergunta_sigilosa,
            response_latency_ms=42.0,
        ),
        expected_output={},
    )
    assert out["comment"] == "duracao_total_da_resposta_conectada_em_ms"
    assert pergunta_sigilosa not in out["comment"]
    assert resposta_sigilosa not in out["comment"]


def test_task_connected_emits_response_latency(monkeypatch):
    monkeypatch.setenv("EVAL_EXPLORE_CONNECTED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")

    import radar.core.services.explore_agent as explore_agent_mod

    class _FakeExploreAgent:
        def explore_with_meta(self, *args, **kwargs):
            return (
                "resposta conectada fake",
                {"route": "EDITAL_FACT", "called_tools": ["graph_explore"]},
            )

    monkeypatch.setattr(explore_agent_mod, "ExploreAgent", _FakeExploreAgent)

    from radar.core.eval import explore as explore_eval

    item = {
        "input": {
            "query": "pergunta fake",
            "target": {"type": "edital", "id": "finep:abc"},
            "workspace": {"id": "ws-test", "profile": {"nome": "x"}},
        }
    }
    for flag in ("false", "true"):
        monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", flag)
        out = explore_eval.task(item=item)
        assert "response_latency_ms" in out, f"grafo {flag} → task conectada emite latência"
        assert out["response_latency_ms"] >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 9. Correção da auditoria KG-P1B-2 — tool_contract NÃO enfraquecido
# ─────────────────────────────────────────────────────────────────────────────

def test_tool_contract_graph_community_alone_fails(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    for route in ("EDITAL_FACT", "EDITAL_FACT_ENUMERATIVE", "ENTITY_FACT"):
        out = explore_eval.eval_tool_contract(
            output=_eval_output(called_tools=["graph_community"]),
            expected_output={"route": route},
        )
        assert out["value"] == 0.0, f"graph_community sozinha não satisfaz {route}"


def test_tool_contract_factual_graph_tools_pass(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    for tool in ("graph_explore", "graph_reason"):
        for route in ("EDITAL_FACT", "ENTITY_FACT"):
            out = explore_eval.eval_tool_contract(
                output=_eval_output(called_tools=[tool]),
                expected_output={"route": route},
            )
            assert out["value"] == 1.0, f"{tool} satisfaz {route}"


def test_tool_contract_graph_community_coexists_with_factual(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_tool_contract(
        output=_eval_output(called_tools=["graph_community", "graph_explore"]),
        expected_output={"route": "EDITAL_FACT"},
    )
    assert out["value"] == 1.0


def test_tool_contract_legacy_tools_still_pass(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    from radar.core.eval import explore as explore_eval

    out = explore_eval.eval_tool_contract(
        output=_eval_output(called_tools=["get_edital"]),
        expected_output={"route": "EDITAL_FACT"},
    )
    assert out["value"] == 1.0
    out = explore_eval.eval_tool_contract(
        output=_eval_output(called_tools=["get_investidor"]),
        expected_output={"route": "ENTITY_FACT"},
    )
    assert out["value"] == 1.0
