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

## Auditoria Codex**Veredito:** condicionado em 2026-07-24 (auditoria da governança — Fable).

Lint/tsc limpos e componente bem construído (acessível, fallback de legado
limpo), porém a integração está ligada a paths que o backend nunca produz
(`deadline`, `mechanism`, `value`, `ticket_range`, `estagio_alvo`,
`lead_follow`, `programs`, `icts`, `investidores`), enquanto os paths reais
(`mecanismo`, `setores`, `tecnologias_tags`, `requisitos_texto.<i>`,
`status`) ficaram sem hint — a feature não renderia nenhum disclosure em
produção. Defeito derivado: o componente retorna null para `unknown` antes
de checar `curationLabel`, então o rótulo de curadoria (decisão 3 do
proprietário) nunca apareceria para campos de catálogo (unknown por
contrato). Co-responsabilidade registrada: o prompt da task descreveu a
forma do dado, não o vocabulário de paths.

Correções exigidas (entregues ao implementador): religar os hints aos paths
reais (incluindo requisitos por item — a única citação documental real),
remover hints de paths inexistentes e de listas de relações, dar precedência
a curationLabel sobre a regra unknown→null, e um hint de origem por ficha de
ator. Aprovação após reauditoria + QA manual do proprietário.

## Correção aplicada

**Implementador da correção:** claude-sonnet (subagente), worktree existente.

Somente os dois arquivos autorizados foram tocados. `frontend/src/types/edital.ts`
não precisou de alteração: `provenance` já era tipado como
`Record<string, FieldProvenance>`, então o acesso por bracket notation
(`detail.provenance?.["mecanismo"]`, `` detail.provenance?.[`requisitos_texto.${i}`] ``)
compila sem mudança de tipo.

### `frontend/src/app/oportunidades/[id]/page.tsx`

1. **Removidos** os hints de `deadline`, `mechanism` (InfoRow "Valor"/"Ticket"
   continuam usando as props `value`/`ticket`, não confundir com o path
   inexistente `mechanism`), `value`, `ticket_range`, `estagio_alvo`,
   `lead_follow`, e das TagCards "Programas", "ICTs relacionadas" e
   "Investidores" (listas de relações — hint conceitualmente errado ali, path
   também não existe no backend).
2. **Religados** aos paths reais do `provenance_writer.py`:
   - InfoRow "Mecanismo" → `detail.provenance?.["mecanismo"]`.
   - TagCard "Temas" → `detail.provenance?.["setores"]` (sem `curationLabel`).
   - TagCard "Tecnologias" → `detail.provenance?.["tecnologias_tags"]` (sem
     `curationLabel`).
   - Seção "Requisitos": hint POR ITEM, um `<ProvenanceHint>` por `<li>` usando
     `` detail.provenance?.[`requisitos_texto.${i}`] `` (índice 0-based, mesma
     ordem do array persistido) — o coração da correção: quando o item tem
     `state="stated"` com `citations`, o tooltip (comportamento já existente do
     componente) mostra "Fonte: <documento>, p. <página>", a única citação
     documental real da ficha.
3. **Fichas de ator** (`kind === "investimento" | "programa"`): um hint de
   origem por ficha, colocado no `<h1>` do título (não existe uma linha
   literal "Fonte oficial" na página — o link condicional mais próximo é "Ver
   página oficial ↗", presente só quando `official_url` existe; o título é o
   único elemento sempre presente em toda ficha de ator, por isso foi o
   ancoradouro escolhido, conforme a alternativa prevista na correção), usando
   `curationLabel="catalogo"` e `detail.provenance?.["name"]`.

### `frontend/src/components/ProvenanceHint.tsx`

4. `curationLabel` agora tem precedência sobre a regra `unknown → null`:
   ```
   if (state === "legacy") return null;
   if (state === "unknown" && !curationLabel) return null;
   ```
   `legacy` continua retornando `null` incondicionalmente (não é um
   `FactState` real do backend — nenhum call site desta task o produz com
   `curationLabel`, então não havia motivo para abrir exceção ali). `unknown`
   só é liberado quando o chamador passa `curationLabel` — é exatamente o
   estado contratual de `build_catalog_copied_provenance` (curado ≠ validado)
   usado pelo path `name` de investidor/programa. Sem `curationLabel`,
   `unknown`/`legacy` continuam retornando `null` — fallback limpo de legado
   inalterado.

   **Consistência componente ↔ call site (ponto de atenção da correção):**
   optou-se pela via mais simples — **passar o objeto de provenance e deixar
   o componente decidir**, mantendo o padrão já usado em todos os outros call
   sites (`{cond && <ProvenanceHint provenance={...} .../>}`). O call site do
   hint de ator segue esse mesmo padrão
   (`detail.provenance?.["name"] && <ProvenanceHint provenance={detail.provenance["name"]} curationLabel="catalogo" />`).
   Não foi necessário tornar a prop `provenance` opcional no componente: como
   `name` é incluído no dict de saída sempre que `record.get("name")` é
   truthy (`build_investidor_fact_provenance`/`build_programa_fact_provenance`),
   na prática o campo estará presente para todo investidor/programa
   curado; se um dia faltar, o guard `&&` no call site já cobre a ausência
   sem renderizar nada — comportamento equivalente e mais simples do que
   aceitar `provenance: undefined` dentro do componente.

### Validação

| Comando/verificação | Resultado |
|---|---|
| `cd frontend && npm run lint` | Limpo — só os 4 warnings preexistentes em `src/lib/auth.tsx` (`react-hooks/exhaustive-deps`, não relacionados a esta task) |
| `cd frontend && npx tsc --noEmit` | Limpo (0 erros, sem output) |
| `git diff --check` | Limpo (exit 0, sem whitespace errors) |
| `git diff 89ff58e7d --stat` | `frontend/src/app/oportunidades/[id]/page.tsx` \| 30 +++++++++++++++++----------- ; `frontend/src/components/ProvenanceHint.tsx` \| 7 ++++++- ; 2 arquivos, ambos autorizados |

O veredito condicionado da auditoria acima permanece intacto (nada foi
reescrito ou apagado). Nenhum arquivo de backend, dependência ou rota foi
tocado. Commit único, nada pushed.

