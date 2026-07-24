# RT01-T11 — Product citations

**Status:** `passed`
**Plano:** [`RT01-T11-product-citations.md`](../../plans/01-provenance/RT01-T11-product-citations.md)
**Branch/commit-base:** `64de00e5d`
**Commits:** `e35aa98db9672981962c71a39ec3351e8ac320d4`
**Implementador/modelo:** deepseek (opencode), worktree isolado

## Realizado

- `frontend/src/types/edital.ts` — tipos aditivos `Citation`, `FieldProvenance` e campo opcional `provenance` em `OportunidadeDetail` (sem refatorar interfaces existentes).
- `frontend/src/components/ProvenanceHint.tsx` — componente único e reutilizável de progressive disclosure (hover tooltip CSS, sem badge/selo visível).
- `frontend/src/app/oportunidades/[id]/page.tsx` — integração nas fichas:
  - `InfoRow` modificado para aceitar `provenance?: FieldProvenance` e renderizar `ProvenanceHint` ao lado do valor.
  - `TagCard` modificado para aceitar `provenance` e `curationLabel` no cabeçalho.
  - Hints adicionados nos campos: `deadline`, `mechanism`, `value`, `ticket_range`, `estagio_alvo`, `lead_follow` (InfoRow) e `programs`, `icts`, `investidores` (TagCard com rótulo de curadoria).

## Decisões do proprietário citadas como contrato

1. **Sem badge/selo.** Estado e origem aparecem SOMENTE em progressive discretion (hover tooltip; primitivo CSS inline, sem dep nova).
2. **Citação documental** mostra só documento + página ("Edital.pdf, p. 17"; sem página → só "Edital.pdf"). Quote NÃO aparece na UI.
3. **Referência de curadoria** tem rótulo distinto: ICT → "Registro oficial EMBRAPII"; investidor/programa → "Catálogo curado do Radar". Nunca formatada como citação documental.
4. **Escopo restrito** às fichas (oportunidade/edital via `/oportunidades/[id]`). Chat do Explorar, cards do Radar, listagens, prompts e backend não tocados.

## Divergências e decisões

- `@radix-ui/react-tooltip` não está nas dependências do projeto. Sem poder adicionar deps novas, o progressive disclosure usa um tooltip CSS inline (hover + estado `useState`), consistente com os tokens do design system (Tailwind). Não há regressão visual pois o layout das fichas permanece idêntico quando provenance está ausente.
- Para estados futuros não previstos (ex.: `conflicting`), o componente renderiza o texto do state genericamente sem UI especial, conforme contrato.

## Dados e migrations

- Não aplicável (frontend apenas).

## Validação

| Comando/verificação | Resultado |
|---|---|
| `cd frontend && npm run lint` | Limpo (warnings preexistentes em `auth.tsx` apenas) |
| `cd frontend && npx tsc --noEmit` | Limpo (0 erros) |
| `git diff --check` | Limpo (sem whitespace errors) |
| `git diff 64de00e5d --stat` | 3 arquivos em `frontend/` |
| `git status` | Apenas os 3 arquivos (1 untracked) + node_modules ausente (worktree) |

## Pendências

- **Testes de componente:** O projeto não possui runner de testes de frontend (0 arquivos `*.test.*` em `src/`). Nenhum framework foi introduzido. O QA manual abaixo descreve a verificação.
- `conflicting` e outros estados futuros têm renderização genérica segura mas nenhuma UI especial construída nesta task.
- O trecho da citação (`quote`) não aparece na UI — fica disponível apenas via API para futura expansão.

## Roteiro de QA manual

Executar com o servidor local rodando (`npm run dev` no frontend + backend em `localhost:8000`):

1. **Edital com citação documental** — Abrir `/oportunidades/finep:589` (ou edital com `provenance` populada pelo backend). Hover no `?` ao lado de "Prazo" → tooltip deve mostrar "Fonte: Edital.pdf, p. 17" (ou similar). Verificar que o tooltip aparece acima do indicador e desaparece ao sair.

2. **ICT com curadoria EMBRAPII** — Abrir `/oportunidades/embrapii:ict-X` (ou entidade ICT com `provenance`). O card "ICTs relacionadas" deve exibir `?` no cabeçalho; hover → "Registro oficial EMBRAPII".

3. **Investidor com curadoria de catálogo** — Abrir `/oportunidades/investidor:kptl` (ou entidade investidora). O card "Investidores" deve exibir `?`; hover → "Catálogo curado do Radar".

4. **Legado sem provenance** — Abrir `/oportunidades/finep:123` (entidade sem campo `provenance` ou com `{}`). Nenhum `?` visível na página; layout idêntico ao anterior à task.

5. **Programa** — Abrir `/oportunidades/programa:centelha` (entidade programa). Card "Programas" deve exibir `?` com "Catálogo curado do Radar" no hover.

## Auditoria Codex

**Veredito:** pendente