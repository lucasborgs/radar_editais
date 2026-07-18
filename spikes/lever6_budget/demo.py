"""Spike Item 6 (guarda por estado) — Task 3: smoke de taxa de truncamento
antes/depois do nó `budget_notice`, por modo (explore, writing).

Throwaway. Dirige um conjunto FIXO de turnos representativos por modo (mesmo
padrão do achado do spike do #2: explore multi-hop forçando list_icts/
list_investidores repetidos; writing pedindo uma seção detalhada que força
search_edital + read_exact_chunk múltiplos) contra os produtores REAIS
(ExploreAgent/WritingSession, mesmos system prompts/tools/`_build_graph`).

Roda a bateria DUAS VEZES:
  1. baseline — `budget_notice` DESLIGADO (monkeypatch de `_build_graph` para a
     topologia de 2 vias pré-Item 6: finalize/agent, sem o nó novo).
  2. treatment — `budget_notice` LIGADO (código real da Task 2).

Mede via o log `turn_end` da Task 1 (grep, não Langfuse) — sempre ligado,
independe de infra externa. Conta `stop_reason=max_steps` por modo e a média
de `llm_calls`/turno.

NÃO decide promover/arquivar o item — só reporta os números em FINDINGS.md.
Essa decisão é da governança (taxa baseline ~0 → gatilho de arquivamento
previsto na spec, mas quem aciona é humano, não este script).

Uso:
    .venv/bin/python3 -m spikes.lever6_budget.demo
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

import core.llm.agent_graph as ag  # noqa: E402
from core.llm.agent_graph import shutdown_writing_runtime  # noqa: E402

# =============================================================================
# Captura do log `turn_end` (Task 1) — handler dedicado, zero dependência de
# Langfuse. Formato: "turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d"
# =============================================================================

_TURN_END_RE = re.compile(
    r"turn_end mode=(?P<mode>\S+) stop_reason=(?P<stop_reason>\S+) "
    r"llm_calls=(?P<llm_calls>\d+) max_steps=(?P<max_steps>\d+)",
)


@dataclass
class TurnEndEvent:
    mode: str
    stop_reason: str
    llm_calls: int
    max_steps: int


class _TurnEndCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.events: list[TurnEndEvent] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = _TURN_END_RE.search(msg)
        if m:
            self.events.append(TurnEndEvent(
                mode=m.group("mode"),
                stop_reason=m.group("stop_reason"),
                llm_calls=int(m.group("llm_calls")),
                max_steps=int(m.group("max_steps")),
            ))


# =============================================================================
# Baseline: reconstrução da topologia de 2 vias PRÉ-Item 6 (sem budget_notice).
# Cópia deliberada de `ag._build_graph` como era antes da Task 2 — só para
# esta comparação; não é código de produção, não substitui o `_build_graph`
# real fora do escopo deste script (monkeypatch é desfeito no fim de cada fase).
# =============================================================================

def _build_graph_no_notice(model, lc_tools, *, max_steps, checkpointer=None):
    bound = model.bind_tools(lc_tools) if lc_tools else model
    tool_node = ag.ToolNode(lc_tools, handle_tool_errors=ag._tool_error_to_str)

    async def agent(state):
        resp = await bound.ainvoke(state["messages"])
        return {
            "messages": [resp],
            "llm_calls": state["llm_calls"] + 1,
            "documents": state.get("documents", {}),
        }

    async def agent_final(state):
        resp = await model.ainvoke(state["messages"])
        return {
            "messages": [resp],
            "llm_calls": state["llm_calls"] + 1,
            "documents": state.get("documents", {}),
        }

    def should_continue(state):
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return ag.END
        return "tools"

    async def tools(state):
        out = await tool_node.ainvoke(state)
        tmsgs = out["messages"]
        for m in tmsgs:
            m.content = ag._cap(
                str(m.content), ag.TOOL_RESULT_CHAR_CAP, tool_name=getattr(m, "name", None),
            )
        return {
            "messages": tmsgs,
            "tool_rounds": state["tool_rounds"] + 1,
            "documents": state.get("documents", {}),
        }

    def after_tools(state):
        # Topologia PRÉ-Item 6: só 2 vias (sem o aviso de penúltimo passo).
        if state["llm_calls"] >= max_steps:
            return "finalize"
        return "agent"

    def finalize(state):
        return {
            "messages": [ag.HumanMessage(content=ag._FINALIZE_PROMPT)],
            "documents": state.get("documents", {}),
        }

    g = ag.StateGraph(ag.AgentState)
    g.add_node("agent", agent)
    g.add_node("agent_final", agent_final)
    g.add_node("tools", tools)
    g.add_node("finalize", finalize)
    g.add_edge(ag.START, "agent")
    g.add_conditional_edges("agent", should_continue, {ag.END: ag.END, "tools": "tools"})
    g.add_conditional_edges("tools", after_tools, {"finalize": "finalize", "agent": "agent"})
    g.add_edge("finalize", "agent_final")
    g.add_edge("agent_final", ag.END)
    return g.compile(checkpointer=checkpointer)


_REAL_BUILD_GRAPH = ag._build_graph


# =============================================================================
# Conjunto fixo de turnos representativos — EXPLORE
# =============================================================================

EXPLORE_QUESTIONS = [
    (
        "Liste TODOS os editais abertos hoje (sem filtrar por tema). Depois, "
        "para os 5 primeiros da lista, me dê a ficha completa de CADA UM "
        "(objetivo, requisitos formais, valores de financiamento em R$, "
        "prazos exatos, TRL exigido) citando os números literalmente — não "
        "resuma nem arredonde, um get_edital por edital. Depois, para os "
        "temas envolvidos, liste ICTs e investidores relevantes."
    ),
    (
        "Quero um panorama completo: liste os editais de biotecnologia e "
        "agtech abertos, com ficha detalhada de cada um (valores, prazos, "
        "TRL). Em seguida liste TODOS os ICTs relevantes para cada tema "
        "encontrado e TODOS os investidores relevantes para cada tema — não "
        "pule nenhum, um list_icts e um list_investidores por tema."
    ),
    (
        "Compare os 4 primeiros editais da lista atual em uma tabela "
        "(objetivo, valor, prazo, TRL, contrapartida) buscando a ficha "
        "completa de cada um individualmente. Depois, para cada edital, "
        "liste ICTs e investidores que fariam sentido como parceiros."
    ),
]


def run_explore_turn(question: str) -> None:
    from core.services.explore_agent import ExploreAgent
    agent = ExploreAgent()
    answer, meta = agent.explore_with_meta(question)
    print(f"    [explore] stop_reason={meta.get('stop_reason')} "
          f"truncated={meta.get('truncated')} answer[:80]={answer[:80]!r}")


# =============================================================================
# Conjunto fixo de turnos representativos — WRITING
# (reproduz o padrão do spike #2: search_edital + read_exact_chunk múltiplos)
# =============================================================================

WRITING_VARIANTS = [
    {
        "profile_key": "tratorbr",
        "edital_id": "finep:769",
        "section_hint": "6. Equipe técnica",
        "instruction": (
            "Escreva a seção 'Equipe técnica' detalhando os perfis necessários. "
            "Antes de escrever, busque no edital (search_edital) os requisitos "
            "formais de equipe/dedicação/titulação e, se precisar do texto "
            "completo de algum trecho, use read_exact_chunk. Cite os trechos "
            "literalmente com os números exatos (percentuais de dedicação, anos "
            "de experiência, titulação exigida). Salve quando fechar."
        ),
    },
    {
        "profile_key": "biotecstartup",
        "edital_id": "finep:769",
        "section_hint": "4. Cronograma físico-financeiro",
        "instruction": (
            "Escreva o 'Cronograma físico-financeiro'. Antes de escrever, busque "
            "no edital (search_edital) os marcos, prazos e formato de desembolso "
            "exigidos; use read_exact_chunk sempre que precisar do texto exato de "
            "uma cláusula. Cite prazos e percentuais literalmente. Salve quando "
            "fechar."
        ),
    },
]


def _load_golden_profile(profile_key: str):
    import json

    from config import ROOT
    from domain.user_profile import CompanyProfile

    data = json.loads((ROOT / "eval_data" / "golden" / "writing_v2.json").read_text(encoding="utf-8"))
    raw = data["profiles"][profile_key]
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    return CompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


def run_writing_variant(variant: dict) -> None:
    import os

    from core.db import get_supabase_service
    from core.services.writing_session import WritingSession

    db = get_supabase_service()
    workspace_id = os.environ["EVAL_WORKSPACE_ID"]
    profile = _load_golden_profile(variant["profile_key"])

    session = WritingSession(
        db=db, workspace_id=workspace_id, profile=profile, edital_id=variant["edital_id"],
    )
    # Turno de priming (F4 plan-first): turn_count==0 cai num branch de plano
    # (LLM direto, SEM passar por run_writing_turn/agent_graph) — não emite
    # turn_end, então não contamina a medição. Necessário só para destravar o
    # branch ReAct normal no 2º turno.
    session.turn("Preciso de ajuda para escrever esta proposta.")
    result = session.turn(variant["instruction"], section_hint=variant["section_hint"])
    print(f"    [writing] success={result.get('success')} "
          f"truncated={result.get('truncated')}")


# =============================================================================
# Bateria + agregação
# =============================================================================

def run_battery(label: str) -> list[TurnEndEvent]:
    capture = _TurnEndCapture()
    graph_logger = logging.getLogger("core.llm.agent_graph")
    graph_logger.addHandler(capture)
    graph_logger.setLevel(logging.INFO)
    try:
        print(f"\n=== bateria: {label} ===")
        for i, q in enumerate(EXPLORE_QUESTIONS, 1):
            print(f"  explore[{i}]")
            try:
                run_explore_turn(q)
            except Exception as e:  # noqa: BLE001 — smoke: isola falha de 1 variante
                print(f"    [explore] FALHOU: {e}")
        for i, v in enumerate(WRITING_VARIANTS, 1):
            print(f"  writing[{i}] ({v['profile_key']}, {v['section_hint']})")
            try:
                run_writing_variant(v)
            except Exception as e:  # noqa: BLE001
                print(f"    [writing] FALHOU: {e}")
    finally:
        graph_logger.removeHandler(capture)
    return capture.events


def summarize(events: list[TurnEndEvent]) -> dict[str, dict]:
    by_mode: dict[str, list[TurnEndEvent]] = defaultdict(list)
    for e in events:
        by_mode[e.mode].append(e)
    out = {}
    for mode, evs in by_mode.items():
        n = len(evs)
        n_trunc = sum(1 for e in evs if e.stop_reason == "max_steps")
        avg_calls = sum(e.llm_calls for e in evs) / n if n else 0.0
        out[mode] = {
            "n_turnos": n, "n_truncados": n_trunc,
            "taxa": (n_trunc / n) if n else 0.0,
            "avg_llm_calls": round(avg_calls, 2),
        }
    return out


def print_table(title: str, summary: dict[str, dict]) -> None:
    print(f"\n--- {title} ---")
    print(f"{'modo':10s} {'#turnos':8s} {'#truncados':11s} {'taxa':6s} {'avg_llm_calls':13s}")
    for mode, s in sorted(summary.items()):
        print(f"{mode:10s} {s['n_turnos']:<8d} {s['n_truncados']:<11d} "
              f"{s['taxa']:<6.2f} {s['avg_llm_calls']:<13.2f}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)  # só o handler dedicado captura turn_end

    # Fase 1 — baseline (budget_notice OFF).
    ag._build_graph = _build_graph_no_notice
    baseline_events = run_battery("baseline (budget_notice OFF)")
    ag._build_graph = _REAL_BUILD_GRAPH

    # Fase 2 — treatment (budget_notice ON, código real).
    treatment_events = run_battery("treatment (budget_notice ON)")

    baseline_summary = summarize(baseline_events)
    treatment_summary = summarize(treatment_events)

    print_table("ANTES (baseline)", baseline_summary)
    print_table("DEPOIS (treatment)", treatment_summary)

    print("\n(números crus para FINDINGS.md)")
    print("baseline_events:", baseline_events)
    print("treatment_events:", treatment_events)

    shutdown_writing_runtime()


if __name__ == "__main__":
    main()
