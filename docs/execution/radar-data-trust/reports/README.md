# Relatórios — Radar Data Trust

Cada implementação gera um relatório Markdown com o mesmo ID e nome-base do
plano correspondente. Logs integrais não são copiados para cá.

Use [`TEMPLATE.md`](TEMPLATE.md); crie o arquivo somente quando a task for
executada.

## Conteúdo mínimo

- status: `passed`, `failed`, `blocked` ou `superseded`;
- branch, commit-base e commits produzidos;
- implementador/modelo;
- escopo realizado;
- divergências e decisões;
- migrations ou dados afetados;
- comandos executados e resumo dos resultados;
- pendências; e
- veredito da auditoria Codex.

Resultados aceitos são movidos para seu seam autoritativo: código em `src/`,
migrations em `supabase/migrations/`, goldens em `data/evaluation/golden/` e
regras em `docs/domain/`.
