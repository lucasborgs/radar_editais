# Spec: frontend chat-first (UI conversacional padrão)

**Decisões fechadas 2026-06-12** (conversa Lucas + Claude):

1. **Chat-first + utilitárias** — sidebar vira lista de conversas; Pipeline e
   Editais sobrevivem como páginas acessíveis por menu discreto; Dashboard e
   Matching são absorvidos pelo chat (cards inline já existem em
   `components/frontdoor/`).
2. **Documento em split-pane Canvas/Artifacts** — painel à direita do chat
   quando a conversa tem documento. A página `/chat` (3-pane) deixa de existir.
3. **Persistir conversas do front door no backend** — reverte a decisão de
   2026-06-11 (sessionStorage); logado = histórico no banco, retomável;
   anônimo = continua efêmero (padrão ChatGPT deslogado).

Filosofia inalterada: "AI drafts, humans decide" — persistência não muda quem
decide (diff de perfil continua aceito/descartado pelo usuário; o backend só
grava o que aconteceu).

---

## Layout-alvo

```
┌──────────┬─────────────────────┬──────────────────────┐
│ Sidebar  │  Conversa (única)   │  Documento (canvas)  │
│ + Nova   │  msgs + cards       │  abre quando há doc  │
│ busca    │  inline (radar,     │  seções, edição      │
│ histórico│  diff, gate)        │  inline, export,     │
│ por data │  composer: 📎, @,   │  checklist (aba)     │
│ ───────  │  model tier         │                      │
│ menu:    │                     │                      │
│ Pipeline │                     │                      │
│ Editais  │                     │                      │
│ Arquivos │                     │                      │
│ Config   │                     │                      │
└──────────┴─────────────────────┴──────────────────────┘
```

**Rotas que morrem**: `/sessions` (vira sidebar, fase 1), `/dashboard` e
`/matching` (absorvidas pelo chat, fase 1), `/chat` (vira canvas, fase 3),
`/library` como página rica (vira anexos + gerenciador mínimo, fase 4).
**Sobrevivem**: `/` (chat), `/pipeline`, `/editais` (+ `[id]`), `/settings`,
`/login`, `/onboarding`.

---

## Modelo de dados (fase 2)

Generalizar `writing_sessions`/`session_turns` em vez de criar tabelas
paralelas — uma conversa é uma conversa; `kind` distingue o sabor. O nome
físico das tabelas NÃO muda (RLS, código e dados existentes ficam intactos);
conceitualmente são "conversations".

### Migração `supabase/migrations/020_conversations.sql`

```sql
alter table public.writing_sessions
  add column if not exists kind  text not null default 'writing'
    check (kind in ('writing', 'frontdoor')),
  add column if not exists title text;

alter table public.writing_sessions alter column edital_id drop not null;
alter table public.writing_sessions
  add constraint writing_sessions_edital_required
  check (kind <> 'writing' or edital_id is not null);

alter table public.session_turns
  add column if not exists entry_kind text not null default 'msg'
    check (entry_kind in ('msg', 'diff', 'radar')),
  add column if not exists payload jsonb;
```

- `title`: exibição no sidebar. Para `frontdoor`, primeira mensagem do usuário
  truncada em 60 chars (título por LLM tier-barato = backlog). Para `writing`,
  permanece derivado do edital (título do edital via lookup, como hoje).
- `entry_kind`/`payload`: persistem as entradas heterogêneas do transcript do
  front door (`types/frontdoor.ts`). `msg` usa `role`+`content` como hoje;
  `diff` e `radar` usam `payload` (JSON da entrada, incl. `status` do diff) com
  `role='assistant'` e `content=''`. `gate` NÃO persiste (só existe para
  anônimo, que não tem persistência).
- RLS existente já cobre (policies por workspace, inalteradas).

## Contratos de API (fase 2)

Router novo `backend/routers/conversations.py` (wiring em `backend/api.py`):

```
GET  /conversations            (auth) → { conversations: [ConversationSummary] }
GET  /conversations/{id}       (auth) → { ...header, entries: [Entry] }
POST /conversations/{id}/entries        (auth) → append entrada não-msg (radar)
PATCH /conversations/{id}/entries/{eid} (auth) → atualiza payload (status do diff)
```

```ts
interface ConversationSummary {
  session_id: string;
  kind: "frontdoor" | "writing";
  title: string | null;          // frontdoor; writing usa edital_title
  edital_id: string | null;
  status: "active" | "completed" | "abandoned";
  turn_count: number;
  created_at: string;
  updated_at: string;
}
interface Entry {
  id: number;                    // session_turns.id
  turn_index: number;
  entry_kind: "msg" | "diff" | "radar";
  role: "user" | "assistant";
  content: string;               // entry_kind=msg
  payload: object | null;        // entry_kind=diff|radar (TranscriptEntry serializada)
}
```

- `GET /conversations` substitui `GET /writing/sessions` no frontend (o
  endpoint antigo permanece até a fase 3 matar os últimos consumidores).
  Implementação: estender `list_sessions` em `core/services/writing_session.py`
  (select de `kind, title`; lookup de `edital_title` continua no front como hoje).
- `DELETE /writing/sessions/{id}` já serve ambos os kinds (mesma tabela) — o
  sidebar usa esse.

### Evolução do `POST /frontdoor/turn`

Auth passa a ser **opcional** (anônimo continua funcionando, rate-limit por IP
inalterado). Request ganha `session_id?: string`; response ganha
`session_id?: string` e `entry_ids?: {...}`.

Comportamento autenticado:
1. Sem `session_id` → cria `writing_sessions` row (`kind='frontdoor'`,
   `workspace_id` do usuário, `title` = mensagem truncada).
2. Persiste turno do usuário (`entry_kind='msg'`) + resposta do assistente
   (`msg`) + diff proposto, se houver (`entry_kind='diff'`,
   `payload={items, status:'pending', origin:'turn'}`).
3. Devolve `session_id` + ids das entradas (o front precisa do id do diff para
   o PATCH no aceite/descarte).

Anônimo: comportamento de hoje, intocado (nada persiste, sessionStorage).
Merge de transcript anônimo→logado no login: **fora de escopo** (só o perfil
migra, fluxo MERGED_FLAG atual).

---

## Fases e gates

### Fase 1 — Shell + sidebar de conversas (frontend puro)

- `ConversationSidebar` substitui `AppSidebar`: botão "Nova conversa" (→ `/`
  com transcript limpo), busca client-side, histórico agrupado por data
  (Hoje/Ontem/7 dias/Antigas) via `listWritingSessions` (até a fase 2 plugar
  `/conversations`), item com hover-menu "..." (excluir, como ChatGPT).
- Clicar numa sessão de escrita → rota de retomada atual (`/chat?session=...`).
- Rodapé do sidebar: menu utilitárias (Pipeline, Editais, Arquivos→`/library`,
  Configurações) + usuário.
- `/sessions`, `/dashboard`, `/matching` → `redirect()` para `/`.
- Gate: `tsc --noEmit` + lint limpos; navegação manual das rotas sobreviventes.

### Fase 2 — Persistência frontdoor (backend + wiring)

- Migração 020 + router `/conversations` + evolução do `/frontdoor/turn`
  (acima) + testes de backend (criar/listar/retomar/patch, RLS por workspace,
  anônimo não persiste).
- Wiring no front: `page.tsx` ganha `sessionId`; entradas novas sincronizam
  (radar via POST entries; aceite/descarte de diff via PATCH); abrir conversa
  `frontdoor` do sidebar carrega entries → `TranscriptEntry[]`. sessionStorage
  vira só o fallback anônimo.
- Gate: testes novos passando + suíte de testes existente verde.

### Fase 3 — Canvas split-pane (depois de 1+2)

- Painel direito na conversa (`kind='writing'`): seletor de seção, edição
  inline + salvar, export, checklist como aba. Mata `/chat`.
- Gate: **`python -m radar.core.eval writing` antes de remover a página legada**
  (coração do produto; padrão do repo para matar superfície legada).

### Fase 4 — Anexos + gerenciador mínimo (depois de 1)

- 📎 no composer (upload → `enrich_content_task`, mesmo caminho de hoje) +
  página "Arquivos" mínima (listar/visualizar/excluir/arquivar). Substitui a
  `/library` rica (996 linhas).
- Gate: `tsc --noEmit` + CRUD manual.

## Fora de escopo (backlog)

- Título de conversa por LLM (tier barato).
- Merge de transcript anônimo→logado.
- Streaming de resposta token a token (UI já compatível, backend não streama).
- Rename manual de conversa no sidebar.
