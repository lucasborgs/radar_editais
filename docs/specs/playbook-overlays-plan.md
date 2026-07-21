# Plano — Estilo de escrita da empresa no Perfil

**Status:** planejado · **Data:** 2026-07-20 · **Fluxo SDD:** plano (Opus) → implementação (Sonnet 5) → governança (Fable) gate/git.
**Fonte:** `docs/specs/langgraph-levers-spec.md` §"Item adjacente" + filosofia "AI age, humano corrige".

> **Nota de nomenclatura.** O item nasceu como "ativar `playbook_overlays` (skills editáveis
> em runtime)". A discussão de produto (2026-07-20, Lucas) redefiniu o escopo: a tabela
> `playbook_overlays` **permanece dormente** e a entrega de hoje é um **campo de estilo de
> escrita no Perfil da empresa**. O arquivo mantém o nome esperado pela governança; o conteúdo
> reflete o escopo real. Justificativa no §"Por que a tabela `playbook_overlays` não é tocada".

---

## Resumo do que foi decidido (sinal, sem ruído)

O sistema hoje não sabe **como** uma empresa gosta de contar sua história na escrita de
propostas. A entrega cria essa memória de **estilo**, preenchida **à mão** pelo dono da
empresa num campo do Perfil, e a injeta no prompt do Redator.

Três memórias já existentes **não são tocadas**:
- `reflection_insights` — memória de **estratégia** (a que editais aplicar; lê só metadados
  de resultado, nunca o texto da proposta). Continua onde está.
- `extract_session_signal` — coletor de atrito de sessão (rejeições do Critic, correções do
  usuário). Continua **desligado** (`AUTO_MEMORY_WRITE=0`).
- `playbook_overlays` (tabela) — camada compartilhada agência/mecanismo. Continua dormente;
  o tier 2 (praxe da agência) segue escrito à mão no git, decisão de Lucas.

**Decisões de produto travadas (2026-07-20):**
| # | Pergunta | Decisão |
|---|---|---|
| P1 | Quais eixos de memória? | Ambos ao longo do tempo; **hoje só o eixo empresa** (estilo). |
| P2 | Quem escreve o texto? | Estilo da empresa: **o dono, num campo do Perfil**. Praxe da agência: **operador, no git**. |
| P3 | Precisa de `ativo`/teto/desligar? | **Não** — é um campo de perfil (valor único, sobrescrevível), não uma tabela que acumula. |
| P4 | RLS vs service-role? | **Resolvido por construção** — o perfil é lido pela conexão do usuário (RLS ativa). |
| — | Extrator automático / botão "aprimorar com insights" / `AUTO_MEMORY_WRITE` | **Adiado.** Lucas revisita no futuro. |

**Restrição dura desta fase:** ZERO consumo de token. Todas as tasks são verificáveis com
teste de montagem/round-trip, sem chamar LLM.

---

## Arquitetura do objeto

- `workspaces.profile` é um **JSONB** ([src/radar/api/common.py:97](../../src/radar/api/common.py#L97)).
  Um campo novo de perfil = uma chave a mais no blob + entrada na allowlist. **Sem migration.**
- O estilo é **craft de escrita**, não dado de matching: entra **só** no prompt do Redator,
  ao lado do bloco `PLAYBOOK DE ESCRITA`. **Não** entra em `CompanyProfile.to_context()`
  (contexto de matching) nem chega ao ComplianceMonitor/Critic.
- A captura do par rascunho→editado (Task 4) é **combustível** para o futuro extrator de
  estilo. É severável: se complicar, corta sem afetar as Tasks 1–3.

---

## Tasks

### Task 1 — Campo `estilo_escrita` no modelo de perfil (backend)
**Depende de:** nada.
**Arquivos:**
- [src/radar/domain/user_profile.py](../../src/radar/domain/user_profile.py) — adicionar `estilo_escrita: str = ""`
  ao dataclass `CompanyProfile` (perto dos campos de descrição, l. ~29-31).
  **NÃO** adicionar em `to_context()` (l. ~78+) — matching não vê estilo.
- [src/radar/api/common.py](../../src/radar/api/common.py) — adicionar `estilo_escrita: str = ""` ao
  `CompanyProfileSchema` (l. ~28); mapear em `to_py_profile` (l. ~54); incluir `"estilo_escrita"`
  no set `allowed` de `profile_from_workspace` (l. ~106-112).

**Critério de aceite (sem token):**
- Teste de round-trip: instanciar `CompanyProfileSchema(estilo_escrita="usa analogias")`,
  passar por `to_py_profile`, conferir que `.estilo_escrita` sobrevive.
- Teste de allowlist: `profile_from_workspace` com um blob contendo `estilo_escrita`
  retorna o campo preenchido; blob sem o campo retorna `""` (default), sem erro.
- Grep-check: `estilo_escrita` NÃO aparece em `to_context()`.

---

### Task 2 — Injetar o estilo no prompt do Redator
**Depende de:** Task 1.
**Arquivos:**
- [src/radar/core/services/writing_session.py](../../src/radar/core/services/writing_session.py) — nos dois pontos
  onde o bloco `PLAYBOOK DE ESCRITA` é montado ([l. 1667](../../src/radar/core/services/writing_session.py#L1667)
  e [l. 2132](../../src/radar/core/services/writing_session.py#L2132)), acrescentar, **quando**
  `self.profile.estilo_escrita` estiver preenchido, um bloco irmão:
  `ESTILO DA EMPRESA (como esta empresa gosta de contar sua história):\n{estilo}`.
  Guardar o texto num atributo resolvido junto de `_playbook_writer_block`
  (l. ~603/649) para não reler o perfil a cada turno.

**Restrições:**
- Só o Redator. NÃO passar para `for_monitor()` / ComplianceMonitor / Critic.
- Bloco vazio quando `estilo_escrita == ""` — regressão-zero para quem não preencheu.

**Critério de aceite (sem token):**
- Teste de montagem: com `profile.estilo_escrita="X"`, a lista de mensagens do Redator
  contém "ESTILO DA EMPRESA" e o texto "X"; com estilo vazio, não contém.
- Teste de não-vazamento: o payload do Monitor (`for_monitor` / auto-review em
  [src/radar/api/routers/writing.py:487](../../src/radar/api/routers/writing.py#L487)) **não** contém o texto de estilo.

---

### Task 3 — Campo na tela de Perfil (frontend)
**Depende de:** Task 1 (schema da API).
**Arquivos:**
- [frontend/src/types/profile.ts](../../frontend/src/types/profile.ts) — adicionar `estilo_escrita: string`.
- [frontend/src/app/perfil/page.tsx](../../frontend/src/app/perfil/page.tsx) — `textarea` com rótulo
  instrutivo, ex.: *"Estilo de escrita — como sua empresa gosta de contar sua história (tom,
  analogias, foco no cliente, o que evitar…). O redator seguirá estas instruções."*
- [frontend/src/components/frontdoor/profileFields.ts](../../frontend/src/components/frontdoor/profileFields.ts) —
  incluir o campo se a lista de campos for compartilhada (verificar; opcional).

**Critério de aceite (sem token):**
- `npx tsc --noEmit` limpo (usar `tsc`, **não** `npm run build` — conflito com dev server,
  memória `feedback_dev_build_conflict`).
- Round-trip manual/local: preencher o campo, salvar, recarregar → texto persiste
  (contra Postgres local, memória `feedback_eval_runs_local`).

---

### Task 4 — Captura do par rascunho→editado (SEVERÁVEL)
**Depende de:** nada. **Corta-se sem afetar 1–3.**
**Propósito:** combustível para o futuro extrator de estilo — para o histórico não nascer
vazio quando o botão "aprimorar estilo com insights" for construído. **Não é lido por nada
nesta fase.**
**Arquivos:**
- [src/radar/core/services/writing_session.py](../../src/radar/core/services/writing_session.py) —
  em `set_section_content` ([l. 2287](../../src/radar/core/services/writing_session.py#L2287)), **antes** de
  sobrescrever `_doc_sections[section_title]`: se já houver rascunho gerado por IA para a seção
  e o novo conteúdo diferir, anexar `{section, ai_draft, user_edited, ts}` a um campo JSONB de
  log na própria linha de `writing_sessions` (ex.: `style_edit_log`). **Sem migration** se
  reusar JSONB existente; se precisar coluna, migration aditiva inerte.

**Restrições:** zero LLM, zero token. Só append em JSONB. Best-effort: falha de persistência
loga e não quebra o save (mesmo padrão do `set_section_content` atual).

**Critério de aceite (sem token):**
- Teste: gerar uma seção (rascunho IA) → salvar versão editada diferente → o log contém 1 par.
- Salvar sem edição real (conteúdo idêntico) → log não cresce.

---

## Fora de escopo (explícito)

- **Extrator de estilo (LLM)**, **botão on/off "Aprimorar estilo com insights das minhas
  sessões"**, e **religar `AUTO_MEMORY_WRITE`** — adiados; Lucas revisita. O botão, quando
  vier, deve ligar num extrator de **estilo** (novo), não no `extract_session_signal` atual,
  que extrai atrito/correção, não estilo.
- **`playbook_overlays` (tabela) e a esteira cross-usuário** (praxe agência/mecanismo destilada
  de vários clientes) — dormente. Precisa de volume de tenants (hoje ~1) e de defesa contra
  vazamento cross-tenant (regra: overlay que não faz sentido sem nomear um cliente não é
  overlay). Ver §abaixo.
- **`reflection_insights` e `extract_session_signal`** — intocados.
- **Runtime/grafo LangGraph** — ortogonal; não tocado.

### Por que a tabela `playbook_overlays` não é tocada
O tier 2 (praxe da agência/mecanismo) foi decidido **permanecer no git**, escrito pelo
operador (como [docs/playbooks/source/finep/global.md](../playbooks/source/finep/global.md) hoje). A
tabela foi desenhada (migration 024) para o caso "sistema destila padrão de vários clientes e
promove como conhecimento global" — exatamente a esteira automática que Lucas adiou. Ativá-la
agora seria construir a casa antes do morador. O reader dormente
([src/radar/core/skills.py:233](../../src/radar/core/skills.py#L233)) segue pronto para quando esse futuro chegar.

---

## Gate diferido (declarado, não omitido)

Injetar o estilo no prompt do Redator **muda a competência que o modelo vê** — mesma classe
das mudanças de playbook. O **eval de writing** que valida essa mudança (o estilo aprendido
não degrada a escrita) **é DIFERIDO para o LOTE FINAL de validação**, junto com o gate do
Item #3 (threads). Motivo: a restrição de zero-token desta fase impede rodar o golden de
writing agora. As Tasks 1–4 entregam com testes de montagem/round-trip; a validação de
qualidade da escrita roda no lote final, antes de considerar o comportamento promovido.

**Dívida herdada a pagar no lote final** (memória `project_writing_agent_evolution`): o writing
golden precisa estar rodável e a métrica de grounding confiável — pré-requisito do gate.
