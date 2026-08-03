"""Golden dos quatro casos motivadores do Explorar.

Default hermético valida rota. Com ``EVAL_EXPLORE_CONNECTED=true`` executa o
pipeline real (retrieval, síntese/agente e catálogo) e julga conteúdo contra o
golden semântico do NotebookLM, com a adaptação temporal da FINEP.

KG-P1B-2 (aditivo, diagnóstico): quando conectado e ``KG_PHASE1_EXPLORE_ENABLED``
ligada, o resultado ganha o bloco estrutural ``phase1`` e a suíte emite
``graph_tool_usage`` (por caso), ``graph_fallback_rate`` e ``graph_latency_ms``
(agregadas da rodada) — sinais estruturais, sem gate/threshold. O
``answer_contract`` é preservado: as graph tools são read-only e aditivas.

Correção da auditoria KG-P1B-2: ``response_latency_ms`` (por caso) mede com
``time.perf_counter()`` a chamada conectada INTEIRA (retrieval+síntese/agente),
independente do grafo — não apenas as graph tools. Hermético → None.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "data" / "evaluation" / "golden" / "explore.json"

GRAPH_TOOL_NAMES = frozenset({"graph_strategy"})
# Graph tools semanticamente adequadas a consulta FACTUAL sobre edital/entidade.
# `graph_community` (cluster) fica FORA: sozinha não responde fato de edital/entidade.
FACTUAL_GRAPH_TOOLS = frozenset({"graph_explore", "graph_reason"})


def load_data() -> list[dict]:
    from radar.core.kg.phase1.tools import reset_run_stats
    from radar.domain.profile_schema import CompanyProfilePayload

    reset_run_stats()
    if not GOLDEN.exists():
        return []
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = []
    for case in payload["cases"]:
        profile = case.get("profile")
        if profile is not None:
            # Golden profile follows exactly the HTTP contract; enriched
            # fixture-only fields are rejected here.
            profile = CompanyProfilePayload.model_validate(profile).model_dump()
        cases.append({
            "input": {"query": case["query"], "target": case.get("target"), "profile": profile},
            "expected_output": {
                "route": case["expected_route"],
                "reference_answer": case["reference_answer"],
            },
            "metadata": {
                "case_id": case["id"], "assertions": case["assertions"],
                "evidence": case["evidence"],
            },
        })
    return cases


def task(*, item: Any, **_) -> dict:
    from radar.core.services.explore_routing import (
        RouteContext,
        profile_strategy_route,
        route_message,
    )

    inp = get_input(item)
    target = inp.get("target") or {"type": "profile", "id": ""}
    if inp.get("profile"):
        route = profile_strategy_route(inp["query"], has_profile=True)
        output = {
            "route": "PROFILE_STRATEGY" if route.value == "profile_strategy" else route.value,
            "decision": {"intent": "PROFILE_STRATEGY", "target_type": "profile"},
        }
        if os.getenv("EVAL_EXPLORE_CONNECTED", "false").lower() != "true":
            return output
        from radar.core.services.explore_agent import ExploreAgent

        started = time.perf_counter()
        answer, meta = ExploreAgent().explore_with_meta(
            inp["query"], profile=inp["profile"], profile_text="perfil autenticado",
        )
        output.update({
            "answer": answer,
            "called_tools": meta.get("called_tools", []),
            "response_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        })
        return output
    decision = route_message(RouteContext(
        message=inp["query"], target_type=target["type"], target_id=target["id"],
    ))
    output = {"route": decision.intent.value, "decision": decision.to_dict()}
    if os.getenv("EVAL_EXPLORE_CONNECTED", "false").lower() != "true":
        return output

    from radar.core.services.explore_agent import ExploreAgent

    kwargs = (
        {"edital_ids": [target["id"]]}
        if target["type"] == "edital"
        else {"node_id": target["id"], "node_type": target["type"]}
    )
    # Auditoria KG-P1B-2: latência da resposta conectada INTEIRA — o timer
    # envolve exclusivamente `explore_with_meta` (não apenas as graph tools).
    started = time.perf_counter()
    answer, meta = ExploreAgent().explore_with_meta(inp["query"], **kwargs)
    response_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    called = meta.get("called_tools", [])
    output.update({
        "answer": answer,
        "route": (meta.get("route_decision") or {}).get("intent", output["route"]),
        "called_tools": called,
        "response_latency_ms": response_latency_ms,
    })
    # KG-P1B-2: sinal estrutural ADITIVO do grafo da Fase 1 (só quando habilitado
    # e conectado) — nunca conteúdo; informa quais graph tools o agente usou.
    from radar.core.kg.phase1.tools import graph_tools_enabled

    if graph_tools_enabled():
        output["phase1"] = {
            "enabled": True,
            "tools_called": [t for t in called if t in GRAPH_TOOL_NAMES],
        }
    return output


def eval_route(*, output, expected_output, **_) -> Evaluation:
    expected = (expected_output or {}).get("route")
    actual = (output or {}).get("route") if isinstance(output, dict) else None
    return {"name": "route_accuracy", "value": 1.0 if actual == expected else 0.0,
            "comment": f"expected={expected}, actual={actual}"}


def eval_tool_contract(*, output, expected_output, **_) -> Evaluation | None:
    if not isinstance(output, dict) or "answer" not in output:
        return None
    route = (expected_output or {}).get("route")
    called = output.get("called_tools") or []
    # Auditoria KG-P1B-2: NÃO enfraquecer o contrato. As graph tools factuais da
    # Fase 1 (graph_explore / graph_reason) são aditivas às do catálogo, mas
    # `graph_community` (cluster) SOZINHA não responde fato sobre edital/entidade
    # — ela pode coexistir com uma tool factual, nunca substituí-la sozinha.
    if route in {"EDITAL_FACT", "EDITAL_FACT_ENUMERATIVE"}:
        expected = "get_edital or search_entities"
        acceptable = {"get_edital", "search_entities"} | set(FACTUAL_GRAPH_TOOLS)
    elif route == "ENTITY_FACT":
        expected = "get_investidor or list_investidores"
        acceptable = {"get_investidor", "list_investidores"} | set(FACTUAL_GRAPH_TOOLS)
    else:
        return None
    passed = bool(acceptable & set(called))
    return {
        "name": "tool_contract", "value": 1.0 if passed else 0.0,
        "comment": f"expected={expected}, called={called}",
    }


def eval_answer_contract(*, output, expected_output, metadata, **_) -> Evaluation | None:
    """Juiz semântico de required/forbidden/conditional, só no modo conectado."""
    if not isinstance(output, dict) or not output.get("answer"):
        return None
    from radar.core.llm.llm_client import make_client

    assertions = (metadata or {}).get("assertions") or {}
    payload = {
        "reference": (expected_output or {}).get("reference_answer"),
        "required": assertions.get("required") or [],
        "forbidden": assertions.get("forbidden") or [],
        "conditional": assertions.get("conditional") or [],
        "answer": output["answer"],
    }
    client = make_client(api_key=os.environ["OPENAI_API_KEY"], max_retries=2)
    response = client.chat.completions.create(
        model=os.getenv("EVAL_EXPLORE_JUDGE_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        messages=[
            {
                "role": "system",
                "content": (
                    "Avalie semanticamente a resposta. Retorne JSON puro com "
                    "required_ok, forbidden_ok, conditional_ok (booleanos), "
                    "missing (lista) e violations (lista). Forbidden só falha "
                    "quando a resposta realmente faz a afirmação proibida."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0,
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    verdict = json.loads(response.choices[0].message.content or "{}")
    passed = all(verdict.get(key) is True for key in (
        "required_ok", "forbidden_ok", "conditional_ok",
    ))
    return {
        "name": "answer_contract", "value": 1.0 if passed else 0.0,
        "comment": json.dumps(verdict, ensure_ascii=False),
    }


def eval_response_latency_ms(*, output, expected_output, **_) -> Evaluation | None:
    """KG-P1B-2 (auditoria) — latência completa da resposta, por caso.

    Mede a chamada conectada INTEIRA (retrieval + síntese/agente), não apenas as
    graph tools — permite comparar a latência total entre duas execuções.
    Hermético (sem `answer`) → None. Numérico, sem threshold/gate. O comentário
    é uma string FIXA: nunca carrega a pergunta, a resposta ou qualquer
    conteúdo do caso."""
    if not isinstance(output, dict) or not output.get("answer"):
        return None
    value = output.get("response_latency_ms")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return {
        "name": "response_latency_ms",
        "value": round(float(value), 2),
        "comment": "duracao_total_da_resposta_conectada_em_ms",
    }


def eval_graph_tool_usage(*, output, expected_output, **_) -> Evaluation | None:
    """KG-P1B-2 — sinal por caso: as graph tools da Fase 1 foram usadas?

    Hermético (sem `answer`) ou grafo desligado → None (não pontua). Só faz
    sentido no modo conectado com `KG_PHASE1_EXPLORE_ENABLED=true`."""
    if not isinstance(output, dict) or not output.get("answer"):
        return None
    from radar.core.kg.phase1.tools import graph_tools_enabled

    if not graph_tools_enabled():
        return None
    called = output.get("called_tools") or []
    used = [t for t in called if t in GRAPH_TOOL_NAMES]
    return {
        "name": "graph_tool_usage", "value": 1.0 if used else 0.0,
        "comment": f"graph_tools_called={used}",
    }


def eval_graph_fallback_rate(*, item_results, **_) -> Evaluation | None:
    """KG-P1B-2 — taxa agregada de fallback das graph tools na rodada
    (calls que degradaram: unavailable/error) sobre o total de calls.

    Diagnóstico estrutural; sem threshold. Sem calls → None (não pontua)."""
    from radar.core.kg.phase1.tools import run_stats

    stats = run_stats()
    calls = sum(int(s.get("calls", 0)) for s in stats.values())
    if not calls:
        return None
    fallbacks = sum(int(s.get("fallbacks", 0)) for s in stats.values())
    return {
        "name": "graph_fallback_rate",
        "value": round(fallbacks / calls, 4),
        "comment": f"fallbacks={fallbacks} calls={calls} tools={sorted(stats)}",
    }


def eval_graph_latency_ms(*, item_results, **_) -> Evaluation | None:
    """KG-P1B-2 — latência média (ms) das graph tools na rodada.

    Diagnóstico estrutural; sem threshold. Sem calls → None (não pontua)."""
    from radar.core.kg.phase1.tools import run_stats

    stats = run_stats()
    calls = sum(int(s.get("calls", 0)) for s in stats.values())
    if not calls:
        return None
    total_ms = sum(float(s.get("duration_ms", 0)) for s in stats.values())
    return {
        "name": "graph_latency_ms",
        "value": round(total_ms / calls, 2),
        "comment": f"calls={calls} tools={sorted(stats)}",
    }


def _prereqs() -> str | None:
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    if os.getenv("EVAL_EXPLORE_CONNECTED", "false").lower() == "true":
        if not (os.getenv("DATABASE_URL") and os.getenv("SUPABASE_URL")
                and os.getenv("SUPABASE_SERVICE_KEY") and os.getenv("OPENAI_API_KEY")):
            return "modo conectado requer DATABASE_URL+SUPABASE+OPENAI"
    return None


SUITE = Suite(
    name="explore",
    description="Rota hermética ou E2E conectado dos quatro casos factuais golden.",
    load_data=load_data,
    task=task,
    evaluators=[eval_route, eval_tool_contract, eval_answer_contract,
                eval_response_latency_ms, eval_graph_tool_usage],
    run_evaluators=[eval_graph_fallback_rate, eval_graph_latency_ms],
    prereqs=_prereqs,
    classification="diagnostic",
    version="4",
    dataset_paths=[GOLDEN],
    expected_cases=4,
    expected_case_ids=[
        "finep-745-itens-financiaveis",
        "fapesc-31-2026-admissibilidade",
        "barn-verticais",
        "barn-tese",
    ],
    manifest_env=[
        "EVAL_EXPLORE_CONNECTED",
        "EVAL_EXPLORE_JUDGE_MODEL",
        "KG_PHASE1_EXPLORE_ENABLED",
        "KG_PHASE1_AUTO_REFRESH_ENABLED",
    ],
)
