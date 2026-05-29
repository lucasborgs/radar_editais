"""Factories de tools para cada agente do Cenário B.

Cada módulo expõe `build_<agente>_tools(state) -> list[Tool]` que captura o
estado necessário (db client, workspace_id, scope, etc.) via closure e devolve
tools prontas para `core.agent_runtime.run_agent`.

Por que closures e não classes globais: tools precisam de RLS/workspace
isolation. Closure por sessão garante que duas WritingSessions concorrentes
nunca compartilham handle de DB ou escopo de busca por engano.
"""
from core.agent_tools.explore_tools import build_explore_tools
from core.agent_tools.writing_tools import build_writing_tools

__all__ = ["build_explore_tools", "build_writing_tools"]
