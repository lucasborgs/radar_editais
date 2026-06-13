# 09 — Carregamento dinâmico de tools (foresight, candidato #3 · ADIADO)

**Fase:** foresight · **Validação:** — (adiado) · **Esforço:** —

## Status: ADIADO

Documentado para registro e com **gatilho explícito**. Não implementar agora.

## Observação

Cada agente recebe um **toolset fixo**, montado no wiring. Com 7–10 tools por
agente (estado atual), o custo de manter todas as definições no contexto é
desprezível e a clareza do toolset fixo vale mais que a indireção de um loader.

Isto é o **espelho, no nível da app, do `ToolSearch`** do harness: em vez de
carregar todas as definições de tool de uma vez, expor um catálogo (nome +
descrição) e carregar o schema só quando o modelo decide usar. Faz sentido **só em
escala** — quando o número de tools cresce a ponto de o overhead de contexto e a
diluição de atenção superarem o custo da indireção.

## Por que adiar

Pagar a complexidade do carregamento dinâmico (catálogo, resolução sob demanda,
cache de schemas) com 7–10 tools é otimização prematura. O ganho é nulo nessa
escala; a indireção só adiciona superfície de bug.

## Gatilho para revisitar

Reabrir esta spec quando **qualquer agente passar de ~20 tools** — aí o overhead de
contexto das definições e a diluição de atenção do modelo começam a doer, e o
padrão `ToolSearch` (catálogo + load sob demanda) passa a pagar.

## Validação

Nenhuma enquanto adiado. Quando reativado: shadow comparando taxa de "carregou a
tool certa" vs toolset fixo, antes de remover a injeção estática.
