"""Factories de tools para cada agente do Cenário B.

Cada módulo expõe `build_<agente>_tools(state) -> list[BaseTool]` (tools nativas
do LangChain, `@tool`) que captura o estado necessário (db client, workspace_id,
scope, etc.) via closure e devolve tools prontas para
`core.llm.agent_runtime.run_agent` (que delega ao grafo LangGraph).

Por que closures e não classes globais: tools precisam de RLS/workspace
isolation. Closure por sessão garante que duas WritingSessions concorrentes
nunca compartilham handle de DB ou escopo de busca por engano.

Padrões transversais (write_todos, scratchpad) ficam em planning_tools /
scratchpad_tools e são appendados por quem precisa.
"""
from core.llm.agent_tools.critic_agent import CriticResult, run_critic
from core.llm.agent_tools.explore_tools import build_explore_tools
from core.llm.agent_tools.planning_tools import PlanState, build_planning_tools
from core.llm.agent_tools.profile_tools import ExtractionState, build_profile_tools
from core.llm.agent_tools.research_tools import build_research_tools
from core.llm.agent_tools.scratchpad_tools import Scratchpad, build_scratchpad_tools
from core.llm.agent_tools.writing_tools import build_writing_tools

__all__ = [
    "CriticResult",
    "ExtractionState",
    "PlanState",
    "Scratchpad",
    "build_explore_tools",
    "build_planning_tools",
    "build_profile_tools",
    "build_research_tools",
    "build_scratchpad_tools",
    "build_writing_tools",
    "run_critic",
]
