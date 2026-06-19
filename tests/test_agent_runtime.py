"""Testes unitários do core.llm.agent_runtime.

Cobre:
  - @tool decorator: inferência de schema, required vs default, erros de
    falta de type hint / docstring
  - Tool.call: erro-como-string, async rejeitado
  - ToolRegistry: dedup, formatos OpenAI e Anthropic
  - run_agent loop: caminho feliz, multi-step, max_steps, tool inexistente,
    erro de LLM, provider inválido

Os adapters _call_openai/_call_anthropic são monkeypatchados — testes não
batem na API real.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm.agent_runtime import (  # noqa: E402
    ToolRegistry,
    TraceStep,
    _format_tool_results_anthropic,
    _format_tool_results_openai,
    _LLMStep,
    _REFLECT_PROMPT,
    resolve_agent_provider,
    run_agent,
    tool,
)


@pytest.fixture(autouse=True)
def _force_legacy_runtime(monkeypatch):
    """Testam o loop LEGADO via stub de `_call_anthropic`. Com o LangGraph como
    default, fixamos o legado (caminho de rollback AGENT_RUNTIME=legacy)."""
    monkeypatch.setenv("AGENT_RUNTIME", "legacy")


# ============================================================================
# @tool decorator
# ============================================================================

def test_tool_decorator_extracts_name_description_schema():
    @tool
    def search_edital(query: str, k: int = 5) -> str:
        """Busca trechos relevantes do edital atual."""
        return ""

    assert search_edital.name == "search_edital"
    assert "Busca trechos" in search_edital.description
    assert search_edital.input_schema["type"] == "object"
    assert set(search_edital.input_schema["properties"]) == {"query", "k"}
    assert search_edital.input_schema["required"] == ["query"]
    assert search_edital.input_schema["properties"]["k"]["default"] == 5


def test_tool_decorator_with_override_name_description():
    @tool(name="kb_search", description="Override description")
    def whatever(x: str) -> str:
        """Docstring ignorada quando description= é dado."""
        return x

    assert whatever.name == "kb_search"
    assert whatever.description == "Override description"


def test_tool_missing_typehint_raises():
    with pytest.raises(TypeError, match="precisa de type hint"):
        @tool
        def broken(query) -> str:  # type: ignore
            """Tool sem type hint no parâmetro."""
            return ""


def test_tool_missing_docstring_raises():
    with pytest.raises(ValueError, match="docstring"):
        @tool
        def no_doc(x: str) -> str:
            return x


def test_tool_no_args_valid():
    @tool
    def list_open_editais() -> str:
        """Sem argumentos — schema vazio mas válido."""
        return "ok"

    assert list_open_editais.input_schema == {
        "type": "object",
        "properties": {},
        "required": [],
    }


# ============================================================================
# Tool.call — erro-como-string
# ============================================================================

def test_tool_call_returns_string():
    @tool
    def add(a: int, b: int) -> str:
        """Soma."""
        return str(a + b)

    assert add.call({"a": 2, "b": 3}) == "5"


def test_tool_call_catches_exception_returns_string():
    @tool
    def boom(x: int) -> str:
        """Sempre quebra."""
        raise ValueError("nada bom")

    result = boom.call({"x": 1})
    assert isinstance(result, str)
    assert "Erro ao executar 'boom'" in result
    assert "nada bom" in result


def test_tool_call_coerces_non_string_return():
    @tool
    def returns_int(x: int) -> str:
        """Retorna int — runtime coage pra str."""
        return x * 2  # type: ignore

    assert returns_int.call({"x": 3}) == "6"


def test_tool_call_async_rejected():
    @tool
    def async_tool(x: int) -> str:
        """Async — runtime sync recusa."""
        import asyncio
        return asyncio.coroutine(lambda: "nope")()  # type: ignore

    # asyncio.coroutine foi removido em 3.11 — alternativa: criar coroutine inline
    async def _real_async():
        return "nope"

    @tool
    def real_async(x: int) -> str:
        """Async real."""
        return _real_async()  # type: ignore[return-value]

    result = real_async.call({"x": 1})
    assert "é async" in result
    assert "call_async" in result


# ============================================================================
# ToolRegistry
# ============================================================================

def test_registry_dedup_raises():
    @tool
    def t1(x: str) -> str:
        """T1."""
        return x

    reg = ToolRegistry()
    reg.add(t1)
    with pytest.raises(ValueError, match="já registrada"):
        reg.add(t1)


def test_registry_openai_schema_shape():
    @tool
    def search(q: str, k: int = 3) -> str:
        """Busca."""
        return ""

    reg = ToolRegistry.from_list([search])
    schemas = reg.to_openai_schema()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "search"
    assert schemas[0]["function"]["description"] == "Busca."
    assert schemas[0]["function"]["parameters"]["properties"]["q"]["type"] == "string"


def test_registry_anthropic_schema_shape():
    @tool
    def get_edital(edital_id: str) -> str:
        """Carrega card."""
        return ""

    reg = ToolRegistry.from_list([get_edital])
    schemas = reg.to_anthropic_schema()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "get_edital"
    assert schemas[0]["description"] == "Carrega card."
    assert "input_schema" in schemas[0]
    assert schemas[0]["input_schema"]["required"] == ["edital_id"]


# ============================================================================
# Format helpers (tool_result blocks por provider)
# ============================================================================

# ============================================================================
# resolve_agent_provider — fallback multi-provider por disponibilidade de key
# ============================================================================

def test_resolve_keeps_preferred_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    provider, model = resolve_agent_provider("anthropic", "claude-sonnet-4-6")
    assert provider == "anthropic"
    assert model == "claude-sonnet-4-6"


def test_resolve_falls_back_to_openai_when_anthropic_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    monkeypatch.delenv("OPENAI_MODEL_AGENT", raising=False)
    monkeypatch.setenv("OPENAI_MODEL_PRO", "gpt-4o")
    provider, model = resolve_agent_provider("anthropic", "claude-sonnet-4-6")
    assert provider == "openai"
    assert model == "gpt-4o"


def test_resolve_explicit_openai_fallback_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    provider, model = resolve_agent_provider(
        "anthropic", "claude-sonnet-4-6", openai_model="gpt-4o-2024-11-20",
    )
    assert provider == "openai"
    assert model == "gpt-4o-2024-11-20"


def test_resolve_raises_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Nenhuma API key"):
        resolve_agent_provider("anthropic", "claude-sonnet-4-6")


def test_format_results_openai_one_per_tool():
    results = [
        {"id": "call_1", "output": "ok"},
        {"id": "call_2", "output": "oops"},
    ]
    formatted = _format_tool_results_openai(results)
    assert len(formatted) == 2
    assert all(m["role"] == "tool" for m in formatted)
    assert formatted[0]["tool_call_id"] == "call_1"
    assert formatted[1]["content"] == "oops"


def test_format_results_anthropic_single_user_message():
    results = [
        {"id": "tool_1", "output": "ok"},
        {"id": "tool_2", "output": "oops"},
    ]
    formatted = _format_tool_results_anthropic(results)
    assert len(formatted) == 1
    assert formatted[0]["role"] == "user"
    blocks = formatted[0]["content"]
    assert len(blocks) == 2
    assert all(b["type"] == "tool_result" for b in blocks)
    assert blocks[0]["tool_use_id"] == "tool_1"
    assert blocks[1]["content"] == "oops"


# ============================================================================
# run_agent loop — adapter stubbado
# ============================================================================

def _make_fake_call(sequence: list[_LLMStep]):
    """Factory: cria um stub que devolve sequence[i] na i-ésima invocação."""
    state = {"i": 0}

    def call(system, messages, tools_schema, model):
        if state["i"] >= len(sequence):
            raise RuntimeError(
                f"fake_call esgotou: {state['i']} chamadas feitas, "
                f"apenas {len(sequence)} programadas"
            )
        step = sequence[state["i"]]
        state["i"] += 1
        return step

    return call


def test_run_agent_no_tool_use_one_step(monkeypatch):
    """Modelo responde direto, sem tools — 1 step, end_turn."""
    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn",
            text="Olá, em que posso ajudar?",
            tool_uses=[],
            assistant_message={"role": "assistant", "content": "Olá, em que posso ajudar?"},
            usage={"input_tokens": 30, "output_tokens": 10},
        ),
    ])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def search(q: str) -> str:
        """Busca."""
        return ""

    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "oi"}],
        tools=[search],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )

    assert result.final_text == "Olá, em que posso ajudar?"
    assert result.stop_reason == "end_turn"
    assert len(result.steps) == 1
    assert result.steps[0].kind == "llm"
    assert result.usage == {"input_tokens": 30, "output_tokens": 10}


def test_run_agent_one_tool_call_then_response(monkeypatch):
    """Modelo chama 1 tool, recebe resultado, responde. 3 steps (llm + tool + llm)."""
    fake = _make_fake_call([
        # Step 1: modelo pede a tool
        _LLMStep(
            stop_reason="end_turn",  # adapter normaliza tool_use → end_turn
            text="Vou buscar.",
            tool_uses=[{"id": "tu_1", "name": "search", "input": {"q": "prazo"}}],
            assistant_message={"role": "assistant", "content": "..."},
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        # Step 2: depois do tool_result, modelo responde
        _LLMStep(
            stop_reason="end_turn",
            text="O prazo é 30/06.",
            tool_uses=[],
            assistant_message={"role": "assistant", "content": "O prazo é 30/06."},
            usage={"input_tokens": 150, "output_tokens": 15},
        ),
    ])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def search(q: str) -> str:
        """Busca."""
        return f"resultado para {q}"

    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "qual o prazo?"}],
        tools=[search],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )

    assert result.final_text == "O prazo é 30/06."
    assert result.stop_reason == "end_turn"
    assert len(result.steps) == 3
    assert result.steps[0].kind == "llm"
    assert result.steps[1].kind == "tool"
    assert result.steps[1].name == "search"
    assert result.steps[1].output == "resultado para prazo"
    assert result.steps[2].kind == "llm"
    assert result.usage["input_tokens"] == 250
    assert result.usage["output_tokens"] == 35


def test_run_agent_unknown_tool_returns_error_string_to_model(monkeypatch):
    """Modelo pede tool que não existe — recebe erro como string, decide o que fazer."""
    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn",
            text="",
            tool_uses=[{"id": "tu_1", "name": "nonexistent", "input": {}}],
            assistant_message={"role": "assistant", "content": "..."},
            usage={"input_tokens": 50, "output_tokens": 10},
        ),
        _LLMStep(
            stop_reason="end_turn",
            text="Desculpe, não consigo.",
            tool_uses=[],
            assistant_message={"role": "assistant", "content": "Desculpe, não consigo."},
            usage={"input_tokens": 60, "output_tokens": 8},
        ),
    ])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def real_tool(x: str) -> str:
        """Tool real."""
        return x

    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "..."}],
        tools=[real_tool],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )

    # O step 1 deve ser o tool com output = erro
    tool_steps = [s for s in result.steps if s.kind == "tool"]
    assert len(tool_steps) == 1
    assert "não existe" in tool_steps[0].output
    assert "real_tool" in tool_steps[0].output  # menciona as disponíveis
    assert result.final_text == "Desculpe, não consigo."


def test_run_agent_max_steps_cuts_infinite_loop(monkeypatch):
    """Modelo fica chamando tool em loop — para em max_steps."""
    # Sempre devolve um tool_use, nunca para
    infinite_tool_use = _LLMStep(
        stop_reason="end_turn",
        text="",
        tool_uses=[{"id": "tu_x", "name": "spin", "input": {}}],
        assistant_message={"role": "assistant", "content": "..."},
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    # Programa 3 steps; max_steps=3 deve cortar
    fake = _make_fake_call([infinite_tool_use, infinite_tool_use, infinite_tool_use])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def spin() -> str:
        """Roda."""
        return "still spinning"

    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "..."}],
        tools=[spin],
        model="claude-sonnet-4-6",
        provider="anthropic",
        max_steps=3,
    )

    assert result.stop_reason == "max_steps"
    # 3 llm + 3 tool = 6 steps
    assert sum(1 for s in result.steps if s.kind == "llm") == 3
    assert sum(1 for s in result.steps if s.kind == "tool") == 3


def test_run_agent_llm_exception_yields_error_stop_reason(monkeypatch):
    """LLM lança exceção — loop termina com stop_reason='error'."""
    def boom(*args, **kwargs):
        raise RuntimeError("API down")

    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", boom)

    @tool
    def noop(x: str) -> str:
        """noop."""
        return x

    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "..."}],
        tools=[noop],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )
    assert result.stop_reason == "error"
    assert result.steps == []
    assert result.final_text == ""


def test_run_agent_unknown_provider_raises():
    @tool
    def t(x: str) -> str:
        """."""
        return x

    with pytest.raises(ValueError, match="provider desconhecido"):
        run_agent(
            system="sys",
            initial_messages=[{"role": "user", "content": "."}],
            tools=[t],
            model="foo",
            provider="bedrock",  # type: ignore[arg-type]
        )


def test_run_agent_on_step_callback_invoked(monkeypatch):
    """Callback on_step recebe cada TraceStep no momento da criação."""
    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn",
            text="tudo bem",
            tool_uses=[],
            assistant_message={"role": "assistant", "content": "tudo bem"},
            usage={"input_tokens": 5, "output_tokens": 3},
        ),
    ])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def t(x: str) -> str:
        """."""
        return x

    captured: list[TraceStep] = []
    run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "oi"}],
        tools=[t],
        model="claude-sonnet-4-6",
        provider="anthropic",
        on_step=captured.append,
    )
    assert len(captured) == 1
    assert captured[0].kind == "llm"
    assert captured[0].text == "tudo bem"


# ============================================================================
# Execução paralela de tools + suporte async (spec 01)
# ============================================================================

def test_tool_call_async_runs_async_tool():
    """call_async aguarda uma tool `async def` e devolve a string (spec 01)."""
    import asyncio

    @tool
    async def afetch(x: int) -> str:
        """Tool async."""
        return f"got {x}"

    out = asyncio.run(afetch.call_async({"x": 7}))
    assert out == "got 7"


def test_run_agent_parallel_tools_wallclock(monkeypatch):
    """2 tools de I/O sync no mesmo turno rodam concorrentes (spec 01):
    wallclock ≈ máx (~0.3s), não a soma (~0.6s)."""
    import time

    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn",
            text="",
            tool_uses=[
                {"id": "tu_1", "name": "slow_a", "input": {}},
                {"id": "tu_2", "name": "slow_b", "input": {}},
            ],
            assistant_message={"role": "assistant", "content": "..."},
            usage={"input_tokens": 10, "output_tokens": 5},
        ),
        _LLMStep(
            stop_reason="end_turn", text="pronto", tool_uses=[],
            assistant_message={"role": "assistant", "content": "pronto"},
            usage={"input_tokens": 10, "output_tokens": 5},
        ),
    ])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def slow_a() -> str:
        """Lenta A."""
        time.sleep(0.3)
        return "a"

    @tool
    def slow_b() -> str:
        """Lenta B."""
        time.sleep(0.3)
        return "b"

    t0 = time.monotonic()
    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "vai"}],
        tools=[slow_a, slow_b],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )
    elapsed = time.monotonic() - t0

    assert result.stop_reason == "end_turn"
    # sequencial seria ~0.6s; concorrente ~0.3s. Folga p/ ruído de agendamento.
    assert elapsed < 0.5, f"esperado <0.5s (concorrente), levou {elapsed:.2f}s"
    # ambas executaram e a ordem do trace casa com a ordem dos tool_uses
    tool_steps = [s for s in result.steps if s.kind == "tool"]
    assert [s.name for s in tool_steps] == ["slow_a", "slow_b"]
    assert [s.output for s in tool_steps] == ["a", "b"]


def test_run_agent_executes_async_tool(monkeypatch):
    """Uma tool `async def` é executada pelo loop (via call_async) e seu
    resultado retorna ao modelo (spec 01)."""
    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn", text="",
            tool_uses=[{"id": "tu_1", "name": "afetch", "input": {"q": "x"}}],
            assistant_message={"role": "assistant", "content": "..."},
            usage={"input_tokens": 1, "output_tokens": 1},
        ),
        _LLMStep(
            stop_reason="end_turn", text="feito", tool_uses=[],
            assistant_message={"role": "assistant", "content": "feito"},
            usage={"input_tokens": 1, "output_tokens": 1},
        ),
    ])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    async def afetch(q: str) -> str:
        """Tool async."""
        return f"async:{q}"

    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "vai"}],
        tools=[afetch],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )

    tool_steps = [s for s in result.steps if s.kind == "tool"]
    assert tool_steps[0].output == "async:x"
    assert result.final_text == "feito"


def test_run_agent_works_within_running_event_loop(monkeypatch):
    """Shim sync (spec 01): run_agent chamado de dentro de um event loop ativo
    roda num worker thread em vez de estourar 'asyncio.run() cannot be called
    from a running event loop'. Cobre o caso de um método sync invocado de uma
    corrotina."""
    import asyncio

    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn", text="ok", tool_uses=[],
            assistant_message={"role": "assistant", "content": "ok"},
            usage={"input_tokens": 1, "output_tokens": 1},
        ),
    ])
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def noop() -> str:
        """noop."""
        return ""

    async def _caller():
        # run_agent é sync, mas aqui há um loop ativo.
        return run_agent(
            system="sys",
            initial_messages=[{"role": "user", "content": "oi"}],
            tools=[noop],
            model="claude-sonnet-4-6",
            provider="anthropic",
        )

    result = asyncio.run(_caller())
    assert result.final_text == "ok"
    assert result.stop_reason == "end_turn"


# ============================================================================
# Reflexão dinâmica (spec 08)
# ============================================================================

def _make_recording_call(sequence: list[_LLMStep]):
    """Como _make_fake_call, mas grava as `messages` recebidas em cada chamada,
    para inspecionar se o prompt de reflexão foi injetado no histórico."""
    state = {"i": 0, "seen": []}

    def call(system, messages, tools_schema, model):
        state["seen"].append(list(messages))
        step = sequence[state["i"]]
        state["i"] += 1
        return step

    return call, state


def _reflected(state) -> bool:
    """True se o _REFLECT_PROMPT apareceu nas messages da 2ª chamada LLM (i.e.,
    foi injetado após a 1ª rodada de tools)."""
    if len(state["seen"]) < 2:
        return False
    return any(
        isinstance(m, dict) and m.get("content") == _REFLECT_PROMPT
        for m in state["seen"][1]
    )


def _one_tool_then_end(tool_name: str, tool_input: dict | None = None):
    """Sequência de 2 steps: chama `tool_name`, depois encerra."""
    return [
        _LLMStep(
            stop_reason="end_turn", text="",
            tool_uses=[{"id": "tu_1", "name": tool_name, "input": tool_input or {}}],
            assistant_message={"role": "assistant", "content": "..."},
            usage={"input_tokens": 1, "output_tokens": 1},
        ),
        _LLMStep(
            stop_reason="end_turn", text="fim", tool_uses=[],
            assistant_message={"role": "assistant", "content": "fim"},
            usage={"input_tokens": 1, "output_tokens": 1},
        ),
    ]


def test_reflection_anticipated_on_plan_change(monkeypatch):
    """Mudança de plano (write_todos) antecipa a reflexão antes do teto."""
    fake, state = _make_recording_call(_one_tool_then_end("write_todos"))
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def write_todos(items: str) -> str:
        """Plano."""
        return "plano atualizado"

    run_agent(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[write_todos], model="claude-sonnet-4-6", provider="anthropic",
        reflect_every=5,  # teto alto: só o sinal de plano pode disparar
    )
    assert _reflected(state)


def test_reflection_anticipated_on_tool_error(monkeypatch):
    """Erro de tool (tool inexistente → string de erro) antecipa a reflexão."""
    fake, state = _make_recording_call(_one_tool_then_end("nao_existe"))
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def real(x: str) -> str:
        """Real."""
        return x

    run_agent(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[real], model="claude-sonnet-4-6", provider="anthropic",
        reflect_every=5,
    )
    assert _reflected(state)


def test_no_reflection_on_trivial_round_before_ceiling(monkeypatch):
    """Rodada trivial (sem erro, sem plano, output pequeno) NÃO dispara reflexão
    enquanto o teto não é atingido."""
    fake, state = _make_recording_call(_one_tool_then_end("search", {"q": "x"}))
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def search(q: str) -> str:
        """Busca."""
        return "ok"

    run_agent(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[search], model="claude-sonnet-4-6", provider="anthropic",
        reflect_every=5,
    )
    assert not _reflected(state)


def test_reflection_fires_at_ceiling(monkeypatch):
    """Teto reflect_every=1 garante reflexão mesmo numa rodada trivial."""
    fake, state = _make_recording_call(_one_tool_then_end("search", {"q": "x"}))
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def search(q: str) -> str:
        """Busca."""
        return "ok"

    run_agent(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[search], model="claude-sonnet-4-6", provider="anthropic",
        reflect_every=1,
    )
    assert _reflected(state)


def test_no_reflection_when_disabled(monkeypatch):
    """reflect_every=None (default) → reflexão desligada (compat)."""
    fake, state = _make_recording_call(_one_tool_then_end("write_todos"))
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", fake)

    @tool
    def write_todos(items: str) -> str:
        """Plano."""
        return "plano"

    run_agent(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[write_todos], model="claude-sonnet-4-6", provider="anthropic",
    )
    assert not _reflected(state)
