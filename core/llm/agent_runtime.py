"""Agent runtime — harness genérico para serviços com tool calling multi-step.

Cenário B (decisão arquitetural 2026-05-29): WritingSession e KGMatch.explore
viram agentes com tools, mantendo os demais serviços LLM-using em 1-shot.

Padrão de uso:

    from core.llm.agent_runtime import tool, run_agent

    @tool
    def search_edital(query: str, k: int = 5) -> str:
        '''Busca trechos relevantes do edital atual.'''
        ...  # retorna string que o modelo vê

    result = run_agent(
        system="Você é um redator de propostas...",
        initial_messages=[{"role": "user", "content": user_message}],
        tools=[search_edital, save_draft, ...],
        model="claude-sonnet-4-6",
        provider="anthropic",
        max_steps=8,
    )
    result.final_text  # resposta do modelo ao usuário
    result.steps       # lista de TraceStep (tool_use + tool_result + assistant)

Princípios:
  • Tools NUNCA lançam exceção pro loop. Erros viram strings ("Erro X: tente Y").
    O modelo decide o que fazer — pular, reformular, desistir.
  • Tools podem precisar de estado (db, workspace_id, etc.). Use factory de tools
    com closures, não classes globais — ver `build_writing_tools(session)`.
  • Schema JSON dos args é inferido via Pydantic a partir dos type hints. A
    description vem da docstring. Nada de schemas escritos à mão (drift).
  • Adapters traduzem o ciclo tool_use/tool_result entre OpenAI (function_call)
    e Anthropic (tool_use blocks). Loop é único.
"""
from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field, create_model

logger = logging.getLogger(__name__)

Provider = Literal["openai", "anthropic"]
StopReason = Literal["end_turn", "max_steps", "max_tokens", "error", "other"]

# Defaults compartilhados com core.llm.llm_client (vide LLM_TIMEOUT_SECONDS / MAX_RETRIES).
_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


# =============================================================================
# Tool dataclass + decorator + schema inference
# =============================================================================

@dataclass
class Tool:
    """Definição de uma tool chamável pelo agente.

    name:         identificador único (vai pro prompt do modelo)
    description:  texto que o modelo lê pra decidir quando usar — é prompt
    input_schema: JSON Schema dos argumentos (gerado via Pydantic)
    func:         callable Python (sync ou async); retorna str sempre
    """
    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., Any]

    def call(self, args: dict[str, Any]) -> str:
        """Executa a tool com args validados pelo schema. Captura toda
        exceção e converte em string — loop nunca quebra por tool ruim."""
        try:
            result = self.func(**args)
            # Suporte preguiçoso a tools async sem ainda usar asyncio.run aqui:
            # o run_agent atual é sync. Se a função for coroutine, exigimos que
            # a refatoração pra async aconteça antes — para já, recusamos.
            if inspect.iscoroutine(result):
                result.close()
                return (
                    f"Erro: tool '{self.name}' é async, runtime atual é sync. "
                    "Use uma versão sync ou aguarde suporte async."
                )
            if not isinstance(result, str):
                return str(result)
            return result
        except Exception as e:
            logger.warning("Tool '%s' falhou: %s", self.name, e)
            return f"Erro ao executar '{self.name}': {e}"


def _infer_input_schema(func: Callable) -> dict[str, Any]:
    """Gera JSON Schema dos parâmetros de `func` via Pydantic.

    Args:
        func: função com type hints completos nos parâmetros.

    Returns:
        JSON Schema (dict) compatível com OpenAI function calling e
        Anthropic tool use — top-level "type": "object" + properties + required.
    """
    sig = inspect.signature(func)
    fields: dict[str, tuple[Any, Any]] = {}

    for name, param in sig.parameters.items():
        if param.annotation is inspect.Parameter.empty:
            raise TypeError(
                f"@tool {func.__name__}: parâmetro '{name}' precisa de type hint"
            )

        # Required quando não tem default; default = ... no Pydantic significa required.
        if param.default is inspect.Parameter.empty:
            default = ...
        else:
            default = param.default

        fields[name] = (param.annotation, Field(default=default))

    if not fields:
        # Tool sem args — schema vazio mas válido.
        return {"type": "object", "properties": {}, "required": []}

    model = create_model(f"{func.__name__}_Args", **fields)
    schema = model.model_json_schema()

    # Pydantic adiciona "title" no top-level e em cada property — verboso e
    # ignorado pelos providers. Limpamos para schema mais enxuto.
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)

    return schema


def tool(
    func: Callable | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable:
    """Decorator que converte uma função em Tool.

    Uso simples:
        @tool
        def search_edital(query: str, k: int = 5) -> str:
            '''Busca trechos do edital.'''
            ...

    Uso com override:
        @tool(name="search", description="busca documentos relevantes")
        def search(q: str) -> str:
            ...

    Devolve o objeto `Tool` (não a função original). Se você quer chamar a
    função diretamente do Python, use `.func(...)` ou guarde a referência
    antes do decorator.
    """
    def wrap(f: Callable) -> Tool:
        tool_name = name or f.__name__
        tool_desc = description or (inspect.getdoc(f) or "").strip()
        if not tool_desc:
            raise ValueError(
                f"@tool {tool_name}: precisa de docstring ou parâmetro description= "
                "— o modelo lê isso pra decidir quando usar"
            )
        return Tool(
            name=tool_name,
            description=tool_desc,
            input_schema=_infer_input_schema(f),
            func=f,
        )

    if func is not None:
        # Forma @tool sem parênteses
        return wrap(func)
    # Forma @tool(name=..., description=...)
    return wrap


# =============================================================================
# Registry — útil para serviços que constroem suas tools dinamicamente
# =============================================================================

@dataclass
class ToolRegistry:
    """Coleção indexada de tools por nome. Útil para o loop de run_agent
    fazer dispatch O(1) de tool_use → callable."""
    tools: dict[str, Tool] = field(default_factory=dict)

    @classmethod
    def from_list(cls, tools: list[Tool]) -> ToolRegistry:
        registry = cls()
        for t in tools:
            registry.add(t)
        return registry

    def add(self, t: Tool) -> None:
        if t.name in self.tools:
            raise ValueError(f"Tool '{t.name}' já registrada")
        self.tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return list(self.tools.keys())

    def to_openai_schema(self) -> list[dict[str, Any]]:
        """Lista de tools no formato esperado pelo SDK OpenAI (function calling)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self.tools.values()
        ]

    def to_anthropic_schema(self) -> list[dict[str, Any]]:
        """Lista de tools no formato esperado pelo SDK Anthropic (tool use)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self.tools.values()
        ]


# =============================================================================
# Trace + resultado do agente
# =============================================================================

@dataclass
class TraceStep:
    """Um passo do loop do agente — chamada LLM OU execução de tool.

    kind == "llm":   modelo gerou texto e/ou pediu tools. `text` tem o texto,
                     `tool_uses` lista o que ele pediu, `usage` traz tokens.
    kind == "tool":  uma tool foi executada em resposta a um tool_use. `name`
                     tem o nome da tool, `input` o dict que veio do modelo,
                     `output` a string devolvida pela tool.
    """
    kind: Literal["llm", "tool"]
    text: str = ""
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Resultado final de run_agent.

    final_text:  o texto da última mensagem do assistant (resposta ao usuário)
    steps:       trace completo do loop (LLM + tools intercalados)
    stop_reason: por que o loop terminou (end_turn = ok; resto = degradado)
    usage:       soma de input_tokens + output_tokens em todas as chamadas LLM
    """
    final_text: str
    steps: list[TraceStep]
    stop_reason: StopReason
    usage: dict[str, int] = field(default_factory=dict)


# =============================================================================
# Adapters por provider — retornam formato comum
# =============================================================================

@dataclass
class _LLMStep:
    """Resultado normalizado de uma chamada LLM (independente de provider)."""
    stop_reason: StopReason
    text: str
    tool_uses: list[dict[str, Any]]            # [{id, name, input}]
    assistant_message: Any                      # para appendar ao messages[]
    usage: dict[str, int]
    raw_response: Any = None                    # resposta crua do SDK (p/ telemetria de custo)


def _call_openai(
    system: str,
    messages: list[dict[str, Any]],
    tools_schema: list[dict[str, Any]],
    model: str,
) -> _LLMStep:
    """Adapter OpenAI Chat Completions (function calling).

    System message é injetado como primeira message (formato OpenAI). Messages
    de input já vêm sem system — o caller (`run_agent`) garante isso.
    """
    from core.llm.llm_client import make_client

    client = make_client(api_key=os.environ["OPENAI_API_KEY"])
    full_messages = [{"role": "system", "content": system}] + messages

    response = client.chat.completions.create(
        model=model,
        messages=full_messages,
        tools=tools_schema if tools_schema else None,
        tool_choice="auto" if tools_schema else None,
    )

    choice = response.choices[0]
    msg = choice.message
    finish_reason = choice.finish_reason or "stop"

    tool_uses: list[dict[str, Any]] = []
    if msg.tool_calls:
        import json
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_uses.append(
                {"id": call.id, "name": call.function.name, "input": args},
            )

    # finish_reason → stop_reason normalizado
    if tool_uses:
        stop = "end_turn"  # ainda há tools a executar; loop continua
    elif finish_reason == "stop":
        stop = "end_turn"
    elif finish_reason == "length":
        stop = "max_tokens"
    else:
        stop = "other"

    # Para appendar de volta às messages, preservamos o tool_calls + content.
    # O SDK aceita o objeto pydantic; convertemos para dict pra ficar agnóstico.
    assistant_msg: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in msg.tool_calls
        ]

    usage = {
        "input_tokens": response.usage.prompt_tokens if response.usage else 0,
        "output_tokens": response.usage.completion_tokens if response.usage else 0,
    }

    return _LLMStep(
        stop_reason="end_turn" if (stop == "end_turn" and not tool_uses) else (
            "other" if stop not in ("end_turn", "max_tokens") else stop
        ),
        text=msg.content or "",
        tool_uses=tool_uses,
        assistant_message=assistant_msg,
        usage=usage,
        raw_response=response,
    )


def _call_anthropic(
    system: str,
    messages: list[dict[str, Any]],
    tools_schema: list[dict[str, Any]],
    model: str,
) -> _LLMStep:
    """Adapter Anthropic Messages API (tool use).

    System message é parâmetro top-level, não vai em messages[].
    """
    from anthropic import Anthropic

    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )

    response = client.messages.create(
        model=model,
        system=system,
        messages=messages,
        tools=tools_schema if tools_schema else [],
        max_tokens=4096,
    )

    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_uses.append({"id": block.id, "name": block.name, "input": block.input})

    # Para appendar de volta, Anthropic exige a message inteira com os blocks
    # originais (text + tool_use) preservando ids.
    assistant_msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": block.text} if block.type == "text"
            else {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            for block in response.content
        ],
    }

    if response.stop_reason == "end_turn":
        stop: StopReason = "end_turn"
    elif response.stop_reason == "tool_use":
        stop = "end_turn"  # tem mais tool — loop continua até modelo dizer end_turn de fato
    elif response.stop_reason == "max_tokens":
        stop = "max_tokens"
    else:
        stop = "other"

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    return _LLMStep(
        stop_reason=stop,
        text="\n".join(text_parts),
        tool_uses=tool_uses,
        assistant_message=assistant_msg,
        usage=usage,
        raw_response=response,
    )


def _format_tool_results_openai(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI: 1 message {"role":"tool"} por resultado."""
    return [
        {"role": "tool", "tool_call_id": r["id"], "content": r["output"]}
        for r in results
    ]


def _format_tool_results_anthropic(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic: 1 message {"role":"user"} com lista de blocks tool_result."""
    return [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r["output"]}
                for r in results
            ],
        }
    ]


# =============================================================================
# Loop principal
# =============================================================================

_REFLECT_PROMPT = (
    "[Reflexão interna — não é mensagem do usuário] "
    "Antes de continuar: em 2 frases, o que você já aprendeu com as buscas feitas? "
    "O que ainda precisa para responder bem ao pedido do usuário?"
)


def resolve_agent_provider(
    preferred: Provider,
    model: str,
    *,
    openai_model: str | None = None,
    anthropic_model: str | None = None,
) -> tuple[Provider, str]:
    """Escolhe (provider, model) conforme as API keys disponíveis no ambiente.

    Resiliência multi-modelo: o sistema não deve depender de um único
    fornecedor. Quando o provider preferido não tem credencial, degrada de
    forma graciosa para o outro (logando em WARNING). O loop do agente é
    provider-agnóstico (ver `run_agent`), então o fallback é transparente.

    Args:
        preferred: provider desejado pelo call site.
        model: modelo desejado para o provider preferido.
        openai_model: modelo a usar se cair para OpenAI. Default: env
            OPENAI_MODEL_AGENT → OPENAI_MODEL_PRO → "gpt-4o" (tool-loops
            pedem um modelo capaz, não o mini).
        anthropic_model: modelo a usar se cair para Anthropic. Default: env
            ANTHROPIC_MODEL_AGENT → "claude-sonnet-4-6".

    Raises:
        RuntimeError: se nenhuma API key de LLM estiver disponível.
    """
    have = {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
    }
    if have[preferred]:
        return preferred, model

    fallback: Provider = "openai" if preferred == "anthropic" else "anthropic"
    if have[fallback]:
        if fallback == "openai":
            fb_model = (
                openai_model
                or os.getenv("OPENAI_MODEL_AGENT")
                or os.getenv("OPENAI_MODEL_PRO")
                or "gpt-4o"
            )
        else:
            fb_model = (
                anthropic_model
                or os.getenv("ANTHROPIC_MODEL_AGENT")
                or "claude-sonnet-4-6"
            )
        logger.warning(
            "resolve_agent_provider: '%s' indisponível (sem API key) — "
            "fallback para '%s' (%s)", preferred, fallback, fb_model,
        )
        return fallback, fb_model

    raise RuntimeError(
        f"Nenhuma API key de LLM disponível (preferido={preferred}, "
        f"fallback={fallback}). Configure ANTHROPIC_API_KEY ou OPENAI_API_KEY."
    )


def run_agent(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[Tool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    on_step: Callable[[TraceStep], None] | None = None,
    reflect_every: int | None = None,
    span_name: str | None = None,
) -> AgentResult:
    """Executa o loop do agente até `end_turn`, `max_steps` ou erro.

    Args:
        system: instrução de sistema (top-level no Anthropic, primeira message no OpenAI)
        initial_messages: histórico inicial (user/assistant), sem system
        tools: lista de Tool decorados via @tool
        model: nome do modelo (ex.: "claude-sonnet-4-6", "gpt-4o")
        provider: "openai" ou "anthropic"
        max_steps: limite de iterações LLM (evita loops infinitos)
        on_step: callback opcional, recebe cada TraceStep recém-criado
        reflect_every: se não-None, injeta um prompt de reflexão após cada N
            rodadas de tool-execution completas. Útil para sessões longas onde
            o agente precisa sintetizar achados antes de continuar.
        span_name: nome do span raiz na telemetria. Default mantém
            `f"agent.{provider}.{model}"`. `run_subagent` passa
            `f"subagent.{name}"` para distinguir subagente de agente top-level
            no trace — não muda comportamento, só o rótulo.

    Returns:
        AgentResult com texto final, trace completo, stop_reason e usage total.
    """
    registry = ToolRegistry.from_list(tools)
    if provider == "openai":
        tools_schema = registry.to_openai_schema()
        format_results = _format_tool_results_openai
        call_llm = _call_openai
    elif provider == "anthropic":
        tools_schema = registry.to_anthropic_schema()
        format_results = _format_tool_results_anthropic
        call_llm = _call_anthropic
    else:
        raise ValueError(f"provider desconhecido: {provider}")

    messages = list(initial_messages)
    steps: list[TraceStep] = []
    total_in = 0
    total_out = 0
    last_text = ""
    stop_reason: StopReason = "max_steps"
    tool_rounds = 0  # rodadas completas de tool-execution (para reflect_every)

    # Import lazy pra evitar custo de telemetria em testes que monkeypatcham
    # adapters — o telemetry helper é leve mas o import puxa langfuse stack.
    from core import telemetry

    with telemetry.agent_run(
        name=span_name or f"agent.{provider}.{model}",
        input={"system": system, "initial_messages": initial_messages},
        metadata={
            "provider": provider,
            "model": model,
            "max_steps": max_steps,
            "tools": registry.names(),
        },
    ) as agent_span:
        for step_idx in range(max_steps):
            with telemetry.llm_generation(
                name=f"llm.step_{step_idx}",
                model=model,
                input=messages,
                metadata={"step_idx": step_idx},
            ) as gen_span:
                try:
                    llm_step = call_llm(system, messages, tools_schema, model)
                except Exception as e:
                    logger.error("run_agent step %d: LLM falhou: %s", step_idx, e)
                    if gen_span is not None:
                        gen_span.update(level="ERROR", status_message=str(e))
                    stop_reason = "error"
                    break

                if gen_span is not None:
                    gen_span.update(
                        output={
                            "text": llm_step.text,
                            "tool_uses": llm_step.tool_uses,
                        },
                    )
                    # usage_details com keys canônicas (input/output + cache/
                    # reasoning) extraídas da resposta crua — viabiliza custo
                    # por turno/sessão no Langfuse (item 8 da auditoria).
                    telemetry.record_usage(gen_span, llm_step.raw_response)

            total_in += llm_step.usage.get("input_tokens", 0)
            total_out += llm_step.usage.get("output_tokens", 0)
            last_text = llm_step.text

            # Trace do step LLM
            llm_trace = TraceStep(
                kind="llm",
                text=llm_step.text,
                tool_uses=llm_step.tool_uses,
                usage=llm_step.usage,
            )
            steps.append(llm_trace)
            if on_step:
                on_step(llm_trace)

            # Sempre appendar a assistant message ao histórico (com tool_calls/blocks)
            messages.append(llm_step.assistant_message)

            # Sem tool_uses → fim do turno
            if not llm_step.tool_uses:
                stop_reason = llm_step.stop_reason
                break

            # Executa cada tool, coleta resultados, appenda ao histórico
            tool_results: list[dict[str, Any]] = []
            for use in llm_step.tool_uses:
                with telemetry.tool_call(
                    name=f"tool.{use['name']}",
                    input=use["input"],
                    metadata={"tool_use_id": use["id"]},
                ) as tool_span:
                    t = registry.get(use["name"])
                    if t is None:
                        output = (
                            f"Erro: tool '{use['name']}' não existe. "
                            f"Tools disponíveis: {', '.join(registry.names())}."
                        )
                    else:
                        output = t.call(use["input"])

                    if tool_span is not None:
                        tool_span.update(output=output)

                tool_results.append({"id": use["id"], "output": output})

                tool_trace = TraceStep(
                    kind="tool",
                    name=use["name"],
                    input=use["input"],
                    output=output,
                )
                steps.append(tool_trace)
                if on_step:
                    on_step(tool_trace)

            messages.extend(format_results(tool_results))

            # Reflexão periódica: após N rodadas de tools, pede síntese ao modelo.
            # O modelo responde como assistant antes de continuar — consolida
            # achados intermediários e ajuda a evitar desvio de objetivo em loops
            # longos.
            tool_rounds += 1
            if reflect_every and tool_rounds % reflect_every == 0:
                messages.append({"role": "user", "content": _REFLECT_PROMPT})
        else:
            # Esgotou max_steps sem o break interno
            stop_reason = "max_steps"
            logger.warning("run_agent: max_steps=%d atingido sem end_turn", max_steps)

        if agent_span is not None:
            agent_span.update(
                output={"final_text": last_text, "stop_reason": stop_reason},
                metadata={
                    "stop_reason": stop_reason,
                    "n_steps": len(steps),
                    "usage": {"input_tokens": total_in, "output_tokens": total_out},
                },
            )

    return AgentResult(
        final_text=last_text,
        steps=steps,
        stop_reason=stop_reason,
        usage={"input_tokens": total_in, "output_tokens": total_out},
    )


# =============================================================================
# Subagente-como-tool — wrapper de degradação graciosa em volta de run_agent
# =============================================================================

def run_subagent(
    *,
    name: str,
    system: str,
    user_message: str,
    tools: list[Tool],
    provider: Provider = "anthropic",
    model: str | None = None,
    max_steps: int = 5,
) -> AgentResult:
    """Roda um subagente-como-tool: resolve provider, executa `run_agent` e
    degrada graciosamente em caso de erro.

    Formaliza o padrão hoje hand-rolled em call sites (ex.: deep_research):
    resolver o provider via API key disponível, encapsular o `run_agent` em
    try/except, e — se algo explodir — devolver um `AgentResult` vazio com
    `stop_reason="error"` em vez de propagar a exceção. O chamador (que é uma
    tool no agente pai) nunca quebra o loop por causa de um subagente.

    Args:
        name: identificador do subagente (ex.: "deep_research") — vira
            `span_name=f"subagent.{name}"` na telemetria e rótulo de log.
        system: system prompt do subagente.
        user_message: a pergunta/tarefa única que dispara o subagente.
        tools: tools internas do subagente.
        provider: provider preferido (resolve_agent_provider faz fallback por key).
        model: modelo desejado; None → default do provider via resolve.
        max_steps: limite de iterações do loop interno (subagentes são curtos).

    Returns:
        AgentResult do loop interno; ou, em falha de resolução/execução,
        AgentResult(final_text="", steps=[], stop_reason="error", usage={}).
    """
    try:
        prov, mdl = resolve_agent_provider(
            provider,
            model or os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6"),
        )
        return run_agent(
            system=system,
            initial_messages=[{"role": "user", "content": user_message}],
            tools=tools,
            model=mdl,
            provider=prov,
            max_steps=max_steps,
            span_name=f"subagent.{name}",
        )
    except Exception as e:
        logger.error("run_subagent '%s' falhou: %s", name, e)
        return AgentResult(
            final_text="", steps=[], stop_reason="error", usage={},
        )
