"""Tool de planejamento (write_todos) — lista de tarefas viva como âncora anti-drift.

Padrão deepagents: em loops longos
o agente perde de vista o objetivo. Uma lista de TODOs que ele mantém e renderiza
a cada update vira âncora no histórico — o estado atual do plano volta no contexto
toda vez que ele escreve.

`write_todos` SUBSTITUI a lista inteira (sem merge incremental). Simplicidade
acima de tudo: o agente reenvia o plano completo com os status atualizados; a
render volta no retorno e fica no histórico. Sem `read_todos` (a render já é o
estado visível).

Princípios (vide core/llm/agent_runtime.py):
  • A tool NUNCA lança exceção pro loop — shape inválido vira string de erro
    explicando o formato.
  • O schema do arg é `list[dict]` solto de propósito: tipar item por item
    geraria $defs aninhados no JSON Schema do Pydantic (verboso, mal suportado).
    A validação real é no corpo da tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.tools import BaseTool, tool

# Status válidos e seus marcadores de render. A ordem define a iconografia.
_STATUS_MARKER = {
    "completed": "✓",
    "in_progress": "▶",
    "pending": "☐",
}
_VALID_STATUS = set(_STATUS_MARKER)


@dataclass
class PlanState:
    """Estado do plano de uma execução (1 turno do writing / 1 extração).

    todos: lista de {"content": str, "status": "pending"|"in_progress"|"completed"}.
    Mutável por closure: build_planning_tools fecha sobre uma instância e a
    tool write_todos a substitui in-place.
    """
    todos: list[dict] = field(default_factory=list)


def _render(todos: list[dict]) -> str:
    """Renderiza a lista de TODOs com marcadores + contagem de concluídas.

    É o retorno que volta ao contexto do modelo — ancora-o no estado do plano.
    """
    if not todos:
        return "Plano vazio (nenhuma tarefa registrada)."
    lines = [
        f"{_STATUS_MARKER[t['status']]} {t['content']}" for t in todos
    ]
    done = sum(1 for t in todos if t["status"] == "completed")
    lines.append(f"\n({done}/{len(todos)} concluídas)")
    return "\n".join(lines)


def build_planning_tools(state: PlanState) -> list[BaseTool]:
    """Constrói a tool de planejamento fechando sobre `state`.

    Cada execução (turno do writing, extração de perfil) cria seu próprio
    PlanState — isolamento por closure, mesmo padrão das demais factories.
    """

    @tool
    def write_todos(todos: list[dict]) -> str:
        """Registra ou atualiza o plano de tarefas da sessão. SUBSTITUI a lista
        inteira a cada chamada — reenvie todas as tarefas com os status atuais,
        não apenas as que mudaram.

        Use em tarefas com múltiplas etapas: registre o plano no início e
        atualize os status conforme avança (marque a tarefa atual como
        in_progress ao começar e completed ao terminar). Em tarefas triviais de
        uma etapa só, não precisa.

        Cada item é um dict:
          • "content" (obrigatório): descrição curta da tarefa, em PT-BR.
          • "status" (opcional): "pending", "in_progress" ou "completed".
            Ausente → "pending".

        Exemplo:
          write_todos([
            {"content": "Buscar requisitos no edital", "status": "completed"},
            {"content": "Redigir a seção de metodologia", "status": "in_progress"},
            {"content": "Salvar o rascunho", "status": "pending"},
          ])

        Retorna o plano renderizado (✓ concluída / ▶ em andamento / ☐ pendente).
        """
        if not isinstance(todos, list):
            return (
                "Erro: 'todos' deve ser uma lista de dicts "
                '{"content": str, "status": "pending"|"in_progress"|"completed"}.'
            )

        normalized: list[dict] = []
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                return (
                    f"Erro no item {i}: cada tarefa deve ser um dict "
                    '{"content": str, "status": ...}, não '
                    f"{type(item).__name__}."
                )
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                return (
                    f"Erro no item {i}: 'content' é obrigatório e deve ser uma "
                    "string não-vazia."
                )
            status = item.get("status", "pending")
            if status not in _VALID_STATUS:
                return (
                    f"Erro no item {i}: 'status' inválido ({status!r}). "
                    f"Use um de: {', '.join(sorted(_VALID_STATUS))}."
                )
            normalized.append({"content": content.strip(), "status": status})

        state.todos = normalized
        return _render(normalized)

    return [write_todos]
