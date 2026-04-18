# Feature: Escrita Híbrida (Estrutura Guiada + Chat por Seção)

## Contexto

O `/chat` atual é um chat livre — o usuário não sabe o que pedir e a proposta não toma forma visível.
A nova estratégia: o sistema conhece as seções obrigatórias do edital (via `section_index` / `facts`)
e apresenta um checklist guiado. O usuário trabalha seção por seção; o chat tem contexto específico
de cada seção.

---

## Requisitos

### Funcionais

**[WH-01]** A tela de escrita exibe um painel lateral esquerdo com o checklist de seções da proposta.

**[WH-02]** Cada seção do checklist tem um status: `pendente` | `rascunho` | `revisado`.

**[WH-03]** Uma barra de progresso no topo indica `X de N seções com rascunho`.

**[WH-04]** O usuário clica em uma seção → o chat à direita é contextualizado para aquela seção específica.

**[WH-05]** Ao abrir uma seção, o sistema envia automaticamente uma mensagem inicial sugerindo o que escrever (baseada nos fatos do edital para aquela seção).

**[WH-06]** O usuário pode marcar uma seção como "revisado" após aprovar o rascunho gerado.

**[WH-07]** O chat mantém histórico separado por seção — trocar de seção não perde o histórico anterior.

**[WH-08]** Existe uma seção especial "Geral" para perguntas sobre o edital que não se encaixam em seção específica.

**[WH-09]** O usuário pode ver e copiar o texto acumulado de todas as seções com rascunho ("Ver proposta completa").

**[WH-10]** A sessão persiste no `sessionStorage` do browser — reload não perde o progresso.

### Seções padrão da proposta

Derivadas dos `section_titles` do edital + seções estruturais fixas de uma proposta FINEP:

| Seção | Fonte |
|---|---|
| Contextualização / Problema | Inferida do edital |
| Objetivos | Inferida do edital |
| Metodologia / Plano de trabalho | Inferida do edital |
| Equipe executora | Perfil da empresa |
| Cronograma | Inferida do edital |
| Orçamento | Inferida do edital |
| Resultados esperados / Impacto | Inferida do edital |

Se o `section_index` do edital tiver títulos que mapeiam para essas seções, usa os títulos reais do edital.

### Não-funcionais

**[WH-11]** O layout é responsivo: em telas menores o checklist colapsa em drawer.

**[WH-12]** A mudança de seção não dispara nova chamada de API — apenas muda o contexto local do chat.

---

## Layout da tela

```
┌─────────────────────────────────────────────────────────────────┐
│ [← Editais]  Chamada de Inovação — Bioeconomia      ████░░ 3/7 │
├──────────────┬──────────────────────────────────────────────────┤
│              │                                                  │
│  SEÇÕES      │  Chat — Contextualização / Problema             │
│  ─────────   │  ────────────────────────────────────────────── │
│  ✓ Contexto  │                                                  │
│  ✓ Objetivos │  [Assistente]: Vou ajudar você a escrever a     │
│  · Metodo... │  seção de Contextualização. Com base no edital, │
│  · Equipe    │  o foco é em soluções para bioeconomia na       │
│  · Cronogr.  │  Amazônia. Qual o problema central que sua      │
│  · Orçamento │  empresa resolve nesse contexto?                │
│  · Resultad. │                                                  │
│  ─────────   │  [Usuário]: ...                                 │
│  ∑ Geral     │                                                  │
│              │  ────────────────────────────────────────────── │
│  [Ver        │  [input] __________________________ [Enviar]    │
│   proposta]  │                                                  │
└──────────────┴──────────────────────────────────────────────────┘
```

---

## Backend — mudanças

### `WritingSession` (novo comportamento por seção)

**Novo campo:** `active_section: str | None` — seção ativa no turno atual.

**`turn()` recebe `section_hint`** (opcional): título da seção ativa no frontend.
- Passa o `section_hint` para o Router LLM como contexto adicional
- O Writer LLM recebe instrução adicional: "você está ajudando a escrever a seção: {section_hint}"

**Novo método: `get_section_starter(section_title: str) -> str`**
- Gera a mensagem inicial automática ao entrar em uma seção
- Busca fatos do edital relevantes para aquela seção
- Retorna texto: "Com base no edital, [contexto da seção]... Como posso começar?"

### `POST /writing/turn` — payload atualizado

```json
{
  "session_id": "...",
  "user_message": "...",
  "section_hint": "Contextualização / Problema"   // novo campo opcional
}
```

### `POST /writing/section-start` — novo endpoint

```json
// Request
{ "session_id": "...", "section_title": "Metodologia" }
// Response
{ "starter_message": "...", "relevant_facts": [...] }
```

---

## Frontend — mudanças

### `app/chat/page.tsx` — refatoração completa da UI

**Estado adicional:**
```typescript
sections: ProposalSection[]       // checklist de seções
activeSection: string | null      // seção selecionada
sectionHistories: Record<string, WritingMessage[]>  // histórico por seção
sectionDrafts: Record<string, string>               // rascunho aprovado por seção
```

**`ProposalSection`:**
```typescript
interface ProposalSection {
  title: string
  status: "pending" | "draft" | "reviewed"
  isGeneral?: boolean
}
```

### `types/api.ts` — atualização
- `WritingTurnRequest` adiciona `section_hint?: string`
- Novo tipo `SectionStartResponse`

---

## Critérios de aceite

- [ ] Abrir `/chat?edital=X` exibe checklist com seções do edital
- [ ] Clicar em seção → chat contextualizado + mensagem inicial automática
- [ ] Trocar de seção e voltar → histórico preservado
- [ ] Seção marcada como "revisado" exibe ✓ no checklist
- [ ] "Ver proposta completa" exibe texto concatenado das seções com rascunho
- [ ] Barra de progresso atualiza ao mudar status de seção
- [ ] Funciona sem seções do edital (fallback: seções padrão FINEP)
