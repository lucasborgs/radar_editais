# 08 — `reflect_every` dinâmico (candidato #5)

**Fase:** 3 (model-routed) · **Validação:** eval escrita · **Esforço:** baixo

## Problema

A reflexão do agente roda em **cadência fixa**: a cada `reflect_every` passos,
dispara independentemente de ter havido progresso ou mudança que justifique
refletir. Passos triviais (uma tool-result curta, uma leitura) podem gatilhar uma
reflexão que não agrega — e passos densos podem ficar longe demais da próxima.

## Estado atual

- `reflect_every` é um contador fixo (`core/llm/agent_runtime.py:689`): a reflexão
  dispara por *número de passos*, não por *necessidade*.

## Mudança proposta

**Reflexão condicional** via heurística leve (determinística, **não LLM** — não
trocar um custo por outro):

- Manter `reflect_every` como **teto** (garante que a reflexão acontece pelo menos
  a cada N passos), mas permitir **antecipar ou pular** com base em sinais baratos:
  - sinais de "vale refletir": erro de tool, mudança de plano (write_todos),
    volume grande de output acumulado desde a última reflexão;
  - sinais de "pode pular": sequência de passos triviais sem novidade.

A heurística decide; a reflexão LLM em si fica igual. Prioridade **baixa** — é
refinamento, não correção.

## Validação

- **Eval gate:** `python -m radar.core.eval writing` — a reflexão existe para melhorar o
  output; reflexão condicional não pode derrubar a qualidade. Comparar score
  baseline (cadência fixa) vs condicional. Promover só se mantém qualidade gastando
  ≤ reflexões.

## Risco

Baixo: no pior caso, a heurística reflete tanto quanto hoje (teto preservado). O
risco é pular uma reflexão que teria corrigido rumo — coberto pelo eval e pelo teto
de segurança.

## Perguntas em aberto

- Quais sinais entram na heurística de "vale refletir"? Começar com
  erro-de-tool + mudança-de-plano (os mais correlacionados com necessidade real) e
  medir.
- Prioridade baixa: implementar só depois de 06/07, ou se o eval de 04/05 apontar a
  reflexão como gargalo de custo.
