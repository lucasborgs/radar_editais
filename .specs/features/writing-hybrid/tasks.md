# Tasks: Escrita Híbrida

## T1 — `WritingSession`: section_hint + section-start (backend)
**O quê:**
- `turn()` aceita `section_hint: str | None` → passa ao Writer LLM como contexto adicional
- Novo método `get_section_starter(section_title) -> str`: busca fatos do edital + gera mensagem inicial
- Histórico permanece único por sessão (não por seção — a separação é responsabilidade do frontend)

**Onde:** `core/writing_session.py`
**Done when:** `session.get_section_starter("Metodologia")` retorna texto não vazio; `turn(msg, section_hint="Objetivos")` inclui o hint no prompt do Writer

---

## T2 — `POST /writing/section-start` + atualizar `/writing/turn` (backend)
**O quê:**
- Novo endpoint `POST /writing/section-start`: chama `session.get_section_starter(section_title)`
- `WritingTurnRequest` adiciona campo `section_hint: str | None`
- `POST /writing/turn` passa `section_hint` para `session.turn()`

**Onde:** `backend/api.py`
**Depende de:** T1
**Done when:** endpoints respondem corretamente via `/docs`

---

## T3 — seções padrão da proposta (frontend utilitário)
**O quê:** Função `getProposalSections(sectionTitles: string[]): ProposalSection[]`
- Recebe os `section_titles` do edital (da resposta de `/writing/start`)
- Tenta mapear para as 7 seções padrão FINEP por keyword matching
- Fallback: usa as 7 seções padrão fixas se `sectionTitles` estiver vazio
- Sempre inclui seção "Geral" ao final

**Onde:** `frontend/src/lib/writing.ts` (novo arquivo)
**Done when:** função retorna 7-8 seções para qualquer input, incluindo array vazio

---

## T4 — refatorar `app/chat/page.tsx` — layout e estado (frontend)
**O quê:** Novo layout de duas colunas:
- Coluna esquerda (240px): `SectionChecklist` com status, barra de progresso, botão "Ver proposta"
- Coluna direita: área do chat (preserva lógica atual de mensagens/input)
- Estado: `sections`, `activeSection`, `sectionHistories`, `sectionDrafts`
- Trocar seção: salva histórico atual, carrega histórico da nova seção (ou vazio)
- Ao selecionar seção: chama `POST /writing/section-start` → insere mensagem inicial no chat

**Onde:** `frontend/src/app/chat/page.tsx`
**Depende de:** T3
**Done when:** layout duas colunas funciona; trocar seção muda contexto do chat e preserva histórico

---

## T5 — status de seção e "Ver proposta completa" (frontend)
**O quê:**
- Botão "Marcar como revisado" no chat → atualiza `sections[i].status = "reviewed"`
- Detecta automaticamente `status = "draft"` quando assistente gera resposta em uma seção
- Modal/drawer "Ver proposta completa": concatena `sectionDrafts` das seções com status ≠ "pending"
- Botão "Copiar tudo" no modal

**Onde:** `frontend/src/app/chat/page.tsx`
**Depende de:** T4
**Done when:** progresso atualiza; modal exibe texto das seções com rascunho

---

## T6 — persistência em sessionStorage (frontend)
**O quê:** Ao mudar `sections`, `sectionHistories`, `sectionDrafts` → salva em `sessionStorage`
com chave `writing_session_{edital_id}`. Ao montar a página → restaura estado se existir.

**Onde:** `frontend/src/app/chat/page.tsx`
**Depende de:** T5
**Done when:** reload não perde histórico nem status das seções

---

## T7 — atualizar `types/api.ts` e `lib/api.ts` (frontend)
**O quê:**
- `WritingTurnRequest` adiciona `section_hint?: string`
- `sendWritingTurn()` aceita e passa `section_hint`
- Nova função `startSectionChat(sessionId, sectionTitle): Promise<SectionStartResponse>`

**Onde:** `frontend/src/types/api.ts`, `frontend/src/lib/api.ts`
**Pode rodar em paralelo com T3**
**Done when:** TypeScript compila sem erros

---

## Ordem de execução

```
Backend:  T1 → T2
Frontend: T3, T7 (paralelo) → T4 → T5 → T6
T2 e T3/T7 podem rodar em paralelo (backend e frontend independentes)
```
