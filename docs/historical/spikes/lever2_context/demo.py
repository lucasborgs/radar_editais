"""Spike Item 2 (Gestão de contexto) — comparador `trim_messages` sobre
históricos REAIS capturados do runtime de produção.

Throwaway. Nada aqui vira código de produção — só mede, contra o `AgentState.
messages` de fato construído por `ExploreAgent`/`WritingSession` (mesmos
system prompts, mesmas tools, mesmo `_build_graph`), o que sobra/falta quando
`trim_messages` (estratégia "last", token_counter real) corta em 2-3
orçamentos diferentes. Ver contrato completo em
docs/specs/langgraph-levers-spec.md, Item 2.

ECONOMIA (mandatada pelo comando do spike): tentamos primeiro reconstruir
históricos do que já está persistido em `session_turns.tool_use` — mas a
maior tool-result persistida lá é de ~2.9k chars e nenhum turno chega perto
de TOOL_RESULT_CHAR_CAP=8000/tool nem de max_steps (ver FINDINGS.md, seção
"Por que geramos ao vivo"). Não representa os casos que hoje sofrem corte, só
o formato (que seria suficiente). Por isso os 2 históricos abaixo são gerados
AO VIVO contra os produtores reais — explore multi-hop força vários get_edital/
list_* na mesma resposta; writing reusa um caso real do golden v2
(eval_data/golden/writing_v2.json) para puxar citações verdadeiras de
search_edital.

Uso:
    .venv/bin/python3 -m spikes.lever2_context.demo
"""
from __future__ import annotations

import json
import re

from dotenv import load_dotenv

load_dotenv()

import core.llm.agent_graph as agent_graph_mod  # noqa: E402
from config import ROOT  # noqa: E402
from core.llm.agent_graph import _build_chat_model, shutdown_writing_runtime  # noqa: E402
from core.llm.agent_runtime import resolve_agent_provider  # noqa: E402

# =============================================================================
# Captura: monkeypatch de `_messages_to_agent_result` (chamado por
# run_agent_graph_async E _writing_turn_async, ambos no mesmo módulo) — stash
# da lista de mensagens LangChain de cada run sem tocar core/.
# =============================================================================

_captured: list[list] = []
_orig_messages_to_agent_result = agent_graph_mod._messages_to_agent_result


def _capture(messages, stop_reason):
    _captured.append(list(messages))
    return _orig_messages_to_agent_result(messages, stop_reason)


agent_graph_mod._messages_to_agent_result = _capture


# =============================================================================
# História A — explore multi-hop (força list_editais + N×get_edital + list_icts
# + list_investidores na MESMA resposta via pergunta comparativa ampla).
# =============================================================================

EXPLORE_QUESTION = (
    "Liste TODOS os editais abertos hoje (sem filtrar por tema). Depois, para "
    "os 5 primeiros da lista, me dê a ficha completa de CADA UM (objetivo, "
    "requisitos formais, valores de financiamento em R$, prazos exatos, TRL "
    "exigido) citando os números literalmente — não resuma nem arredonde, um "
    "get_edital por edital. Depois, para os temas envolvidos, liste ICTs e "
    "investidores relevantes."
)


def build_explore_history() -> list:
    from core.services.explore_agent import ExploreAgent

    _captured.clear()
    agent = ExploreAgent()
    answer, meta = agent.explore_with_meta(EXPLORE_QUESTION)
    print(f"[explore] stop_reason={meta.get('stop_reason')} "
          f"truncated={meta.get('truncated')} tools={meta.get('called_tools')}")
    print(f"[explore] final_text[:200]={answer[:200]!r}")
    assert _captured, "explore: captura vazia — _messages_to_agent_result não foi chamado"
    return _captured[-1]


# =============================================================================
# História B — writing com citações. NÃO cria sessão nova: uma sessão fresca
# cai no branch F4 "plan-first" do primeiro turno (_first_turn_with_generation
# — gera só um plano, sem tool call nenhuma, não captura nada útil). Em vez
# disso RETOMA uma sessão real já existente no workspace de eval (criada por
# uma run anterior do golden v2, mesmo workspace EVAL_WORKSPACE_ID) — turn_count
# > 0 pula o branch de plano e vai direto pro ReAct real (search_edital).
# =============================================================================

_EXISTING_SESSION_ID = "3457abbd-c2a5-4135-97e8-a3a2dc6aac7d"  # finep:769, ws=EVAL_WORKSPACE_ID


def _load_golden_profile(profile_key: str) -> dict:
    data = json.loads((ROOT / "eval_data" / "golden" / "writing_v2.json").read_text(encoding="utf-8"))
    return data["profiles"][profile_key]


def build_writing_history() -> list:
    import os

    from core.db import get_supabase_service
    from core.services.writing_session import WritingSession
    from domain.user_profile import CompanyProfile

    profile_raw = _load_golden_profile("tratorbr")
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    profile = CompanyProfile(**{k: v for k, v in profile_raw.items() if k in allowed})

    db = get_supabase_service()
    workspace_id = os.environ["EVAL_WORKSPACE_ID"]

    _captured.clear()
    session = WritingSession(
        db=db, workspace_id=workspace_id, profile=profile, session_id=_EXISTING_SESSION_ID,
    )
    instruction = (
        "Escreva a seção 'Equipe técnica' detalhando os perfis necessários. "
        "Antes de escrever, busque no edital (search_edital) os requisitos "
        "formais de equipe/dedicação/titulação e cite os trechos literalmente "
        "com os números exatos (percentuais de dedicação, anos de experiência, "
        "titulação exigida). Salve quando fechar."
    )
    result = session.turn(instruction, section_hint="6. Equipe técnica")
    print(f"[writing] success={result.get('success')} truncated={result.get('truncated')} "
          f"n_tool_calls={len(result.get('tool_trace') or [])}")
    print(f"[writing] answer[:200]={(result.get('assistant_message') or '')[:200]!r}")
    assert _captured, "writing: captura vazia — _messages_to_agent_result não foi chamado"
    # Pega o run com MAIS mensagens (mais chance de ter passado por vários
    # tool rounds) — turn() pode disparar mais de um run interno (ex. critic).
    return max(_captured, key=len)


# =============================================================================
# Análise: densidade de citação + trim_messages em 3 orçamentos
# =============================================================================

_CITATION_PATTERNS = [
    re.compile(r"R\$\s?[\d.,]+"),
    re.compile(r"\bTRL\s?\d"),
    re.compile(r"\d+\s?%"),
    re.compile(r"\bart\.?\s?\d+", re.IGNORECASE),
    re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}"),
    re.compile(r"\d+\s?(dias|meses|anos)\b", re.IGNORECASE),
]


def citation_hits(text: str) -> int:
    return sum(len(p.findall(text)) for p in _CITATION_PATTERNS)


def _msg_kind(m) -> str:
    return type(m).__name__


def _msg_len(m) -> int:
    c = m.content
    return len(c) if isinstance(c, str) else len(str(c))


def describe_history(label: str, messages: list) -> None:
    print(f"\n=== {label}: {len(messages)} mensagens ===")
    for i, m in enumerate(messages):
        name = getattr(m, "name", None) or ""
        hits = citation_hits(str(m.content))
        tag = " CITAÇÃO" if hits else ""
        print(f"  [{i}] {_msg_kind(m):14s} {name:20s} len={_msg_len(m):5d} hits={hits}{tag}")


def _trim_and_report(messages: list, chat_model, budget: int, total_tokens: int, *, start_on) -> None:
    from langchain_core.messages.utils import trim_messages

    trimmed = trim_messages(
        messages,
        max_tokens=budget,
        token_counter=chat_model,
        strategy="last",
        start_on=start_on,
        include_system=True,
    )
    kept_ids = {id(m) for m in trimmed}
    dropped = [m for m in messages if id(m) not in kept_ids]
    dropped_hits = sum(citation_hits(str(m.content)) for m in dropped)
    kept_hits = sum(citation_hits(str(m.content)) for m in trimmed)
    pct = 100 * budget / total_tokens if total_tokens else 0
    tag = f"start_on={start_on!r}" if start_on else "sem start_on"
    print(f"  budget={budget:5d} ({pct:5.1f}% do total) [{tag}] → mantidas={len(trimmed)}/"
          f"{len(messages)} msgs | hits mantidos={kept_hits} perdidos={dropped_hits}")
    if dropped_hits:
        for m in dropped:
            h = citation_hits(str(m.content))
            if h:
                name = getattr(m, "name", None) or _msg_kind(m)
                print(f"      PERDEU {h} hit(s) em {name}: {str(m.content)[:120]!r}")


def run_trim_experiment(label: str, messages: list, chat_model, budgets: list[int]) -> None:
    total_tokens = chat_model.get_num_tokens_from_messages(messages)
    total_hits = sum(citation_hits(str(m.content)) for m in messages)
    print(f"\n--- {label}: trim_messages (total={total_tokens} tokens, "
          f"{total_hits} hits de citação em {len(messages)} msgs) ---")

    # Comparação deliberada (achado do run ao vivo, ver FINDINGS): start_on="human"
    # é o padrão pensado pra memória multi-turno (alinhar o corte a uma borda de
    # turno) mas é traiçoeiro no shape intra-turno do ReAct daqui (1 HumanMessage
    # seguido de uma cadeia longa de Tool/AIMessage) — pode colapsar pra quase
    # nada assim que o orçamento força a excluir esse único ponto de ancoragem.
    for budget in budgets:
        _trim_and_report(messages, chat_model, budget, total_tokens, start_on="human")
        _trim_and_report(messages, chat_model, budget, total_tokens, start_on=None)


# =============================================================================
# Protótipo opcional: nó de resumo seletivo por densidade
# =============================================================================

def summarize_low_density(messages: list, chat_model, *, density_threshold: int = 1) -> None:
    """Para cada ToolMessage de baixa densidade de citação (hits < threshold),
    chama gpt-4o-mini para resumir a ~250 chars preservando qualquer número
    citado. ToolMessages de alta densidade (fonte normativa — get_edital/
    search_edital com hits) NÃO são tocadas. Mede o custo em tokens do resumo
    para informar o FINDINGS."""
    from langchain_core.messages import HumanMessage, ToolMessage

    candidates = [
        m for m in messages
        if isinstance(m, ToolMessage) and citation_hits(str(m.content)) < density_threshold
        and len(str(m.content)) > 400
    ]
    if not candidates:
        print("\n--- resumo seletivo: nenhum ToolMessage de baixa densidade "
              "grande o bastante para valer a pena ---")
        return

    print(f"\n--- resumo seletivo: {len(candidates)} candidato(s) (baixa densidade) ---")
    total_in = total_out = 0
    for m in candidates:
        name = getattr(m, "name", None) or "tool"
        prompt = (
            "Resuma o resultado de tool abaixo em até 250 caracteres. "
            "PRESERVE VERBATIM qualquer número, valor em R$, prazo, percentual "
            "ou sigla técnica (TRL, art.) que aparecer — nunca arredonde ou "
            "generalize um número.\n\n"
            f"Tool: {name}\n\nConteúdo:\n{str(m.content)[:4000]}"
        )
        resp = chat_model.invoke([HumanMessage(content=prompt)])
        um = resp.usage_metadata or {}
        total_in += um.get("input_tokens", 0)
        total_out += um.get("output_tokens", 0)
        print(f"  {name}: {len(str(m.content))} chars → {len(resp.text)} chars "
              f"(custo: {um.get('input_tokens', 0)}in/{um.get('output_tokens', 0)}out)")
        print(f"    antes: {str(m.content)[:100]!r}")
        print(f"    depois: {resp.text[:100]!r}")
    print(f"  custo total do resumo: {total_in} input + {total_out} output tokens "
          f"({len(candidates)} chamada(s))")


def main() -> None:
    provider, model = resolve_agent_provider("anthropic", "claude-sonnet-4-6")
    print(f"provider resolvido={provider} model={model}")
    chat_model = _build_chat_model(provider, model, temperature=0)

    explore_history = build_explore_history()
    describe_history("EXPLORE (multi-hop)", explore_history)
    total_explore_tokens = chat_model.get_num_tokens_from_messages(explore_history)
    run_trim_experiment(
        "EXPLORE", explore_history, chat_model,
        budgets=[
            int(total_explore_tokens * 0.9),
            int(total_explore_tokens * 0.6),
            int(total_explore_tokens * 0.35),
        ],
    )

    writing_history = build_writing_history()
    describe_history("WRITING (citações)", writing_history)
    total_writing_tokens = chat_model.get_num_tokens_from_messages(writing_history)
    run_trim_experiment(
        "WRITING", writing_history, chat_model,
        budgets=[
            int(total_writing_tokens * 0.9),
            int(total_writing_tokens * 0.6),
            int(total_writing_tokens * 0.35),
        ],
    )

    print("\n\n########## PROTÓTIPO OPCIONAL: resumo seletivo ##########")
    summarize_low_density(explore_history, chat_model)
    summarize_low_density(writing_history, chat_model)

    shutdown_writing_runtime()


if __name__ == "__main__":
    main()
