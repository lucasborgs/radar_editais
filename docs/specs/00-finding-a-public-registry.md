# 00 — Registro público de tools incompleto (Finding A)

**Fase:** 0 (trivial) · **Validação:** teste de import · **Esforço:** trivial

## Problema

`core/llm/agent_tools/__init__.py` se apresenta como o índice das factories de
tools, mas dois produtores reais de tools ficam de fora do `__all__` e são
consumidos por *late import* dentro de `writing_tools.py`:

- `build_research_tools` — `research_tools.py:21`, importado em `writing_tools.py:358`
- `run_critic` / `CriticResult` — `critic_agent.py:184,178`, importado em `writing_tools.py:285`

Efeito: o registro mente sobre a superfície real. Quem lê o `__init__` não
descobre `deep_research` nem o critic.

## Estado atual

`__init__.py` `__all__` (linhas 20-28): `ExtractionState, PlanState, Scratchpad,
build_explore_tools, build_planning_tools, build_profile_tools,
build_scratchpad_tools, build_writing_tools`.

## Mudança proposta

1. **Antes de tudo, verificar ciclo de import:** `critic_agent` e `research_tools`
   importam de `writing_tools`/`writing_session`? Se sim, importar no `__init__`
   cria ciclo — nesse caso, **não** mover; em vez disso documentar no docstring do
   `__init__` que são internos-only e por quê.
2. Se não houver ciclo: adicionar ao `__init__`:
   - `from .research_tools import build_research_tools`
   - `from .critic_agent import run_critic, CriticResult`
   e incluí-los no `__all__`.

## Validação

Novo `tests/test_agent_tools_registry.py`: assert que cada `build_*_tools`/factory
pública existente no pacote está em `__all__` (ou explicitamente numa allowlist de
"internos documentados"). Trava o drift futuro.

## Risco

Baixo. Único risco real = ciclo de import — por isso o passo 1 é obrigatório.
