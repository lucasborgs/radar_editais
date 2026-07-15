# Spec UX — Front-door conversacional (Fase 3 · 1a)

> Decisões de produto registradas em **2026-06-11** (Lucas + sessão de spec).
> Escopo: a **1a** do ROADMAP — porta de entrada conversacional que substitui
> onboarding + tela de match. A **1b** (workspace tipo IDE) é outro projeto e
> fica fora deste documento; aqui só definimos a fronteira com ela.

## 1. Objetivo

Uma interface de **chat puro** onde qualquer pessoa — logada ou não — conversa
com a base de conhecimento, constrói o perfil da empresa na própria conversa e
recebe as fontes (editais / desafios / programas / investidores) **rankeadas
pelo radar unificado**, tudo inline. Elimina o formulário de onboarding e a
tela de matching como passos separados.

## 2. Decisões de produto (fechadas)

| # | Decisão | Escolha | Implicação principal |
|---|---|---|---|
| D1 | Layout | **Chat puro, resultados inline** (sem painel lateral) | radar aparece como cards na conversa; precisa de âncora "ver ranking atual" para não se perder no scroll |
| D2 | Alcance | **Porta pública** (anônimo conversa e vê ranking completo) | perfil anônimo vive no navegador; rate-limit e tier de modelo p/ custo |
| D3 | Gates de login | **(a)** upload de anexos · **(b)** persistir perfil entre visitas · **(c)** avançar p/ escrita/workspace | ranking completo é livre; brief GO/NO-GO também exige conta (persiste em `application_log`) |
| D4 | Motor | **Híbrido**: agente conversa e **propõe** mudanças de perfil; usuário confirma; o **front dispara** `/match/radar` deterministicamente após confirmação | alinhado a "AI drafts, humans decide"; o agente nunca roda o match sozinho |
| D5 | Confirmação de perfil | **Card de diff inline** na conversa (`+ campo`, `~ campo`, aceitar/editar) | o aceite é o trigger do refresh do radar |
| D6 | Clique no match | **Expansão inline** no chat (detalhe público + razões do match); "ver tudo" → página de detalhe atual | usuário não sai do fluxo; brief completo é ação logada dentro da expansão |
| D7 | Home | **Home única para todos** (`/`) | `/onboarding`, `/matching`, `/dashboard` e `/chat` são aposentados (redirect); `/editais`, `/library`, `/sessions` viram navegação secundária até a 1b |

### Stack (decisão técnica, delegada ao modelo)

**Manter o app Next.js 14 atual e evoluir in-place.** Racional: o app já tem
auth (Supabase JWT), client de API (`frontend/src/lib/api.ts`), primitivos
(Radix/Tailwind/sonner) e um pacote de chat (`components/chat`) — recomeçar
re-paga tudo isso sem ganho para um produto solo-maintained. O front-door
nasce como a nova rota `/` e as telas antigas morrem por redirect, não por
rewrite big-bang. Upgrade de framework (Next 15+) é ortogonal e fica para
depois, se necessário. Convenções existentes valem (memória
`project_frontend_conventions`): sonner para toasts, primitivos
Modal/Tabs/Skeleton, chat unificado em `components/chat`.

## 3. Anatomia da tela

Uma coluna, mobile-first por construção. Três regiões:

```
┌──────────────────────────────────────────┐
│ ◤ Radar de Editais        [entrar] [⋮]   │  header fino: logo, login/avatar,
│ Perfil ▓▓▓░ 60% · ver radar atual ↓      │  + barra de status (completude do
├──────────────────────────────────────────┤    perfil + atalho p/ último ranking)
│                                          │
│  🤖 Oi! Me conte o que sua empresa faz   │  transcript: turnos + cards
│     — ou cole a URL do site.             │  (diff de perfil, radar, expansão)
│  👤 Somos uma healthtech de diagnóstico… │
│  🤖 [card: diff de perfil]               │
│  🤖 [card: radar top-5]                  │
│                                          │
├──────────────────────────────────────────┤
│ [✎ digite…]                    [📎] [⏎]  │  composer: texto + anexo (gate de
└──────────────────────────────────────────┘  login no 📎 p/ anônimo)
```

- **Barra de status** (sob o header): completude do perfil por quadrante +
  link "ver radar atual" que rola até (ou re-posta) o último card de radar.
  É a resposta ao risco do D1 (ranking se perde no scroll) sem virar painel.
- **Estado vazio (first-run):** mensagem de boas-vindas do agente + 3–4 chips
  de sugestão ("colar URL do site", "descrever a empresa", "explorar o que
  existe pra IA em saúde"). Sem hero separado — é chat desde o primeiro pixel.

### Tipos de card no transcript

| Card | Conteúdo | Ações |
|---|---|---|
| **Diff de perfil** | linhas `+`/`~` por campo do `CompanyProfile` (setor, TRL, porte, UF, estágio…) | `✓ Aceitar` · `✎ Editar` (abre os campos do diff editáveis inline) · descartar |
| **Radar** | top-5 do `/match/radar`: título, badge de tipo (edital/desafio/programa/investidor), badge "não verificado" p/ `provisorio`, score, 1 linha de "por quê" + sinal "por que agora" | expandir item · "ver ranking completo" (expande a lista p/ top-k no próprio card) |
| **Expansão de item** | detalhe do catálogo (público) + razões do match para ESTE perfil; prazo, valores, elegibilidade | "ver página completa" → `/editais/{id}` · "gerar brief GO/NO-GO" (logado) · "começar proposta" (logado, → 1b futura; hoje abre `/sessions` flow) |
| **Aviso de gate** | quando ação exige conta: "crie conta para anexar/salvar/escrever" | login/signup (preserva conversa e perfil ao voltar) |

## 4. Fluxos

### F1 — Visitante anônimo, primeira visita
1. Cai em `/` → estado vazio com chips.
2. Descreve a empresa (ou cola URL → `POST /profile/extract`, já público).
3. Agente responde + posta **card de diff** com o que extraiu.
4. Usuário aceita → perfil (client-side, `localStorage`) atualiza → **se**
   `is_complete()` (nome + descricao_atividades), front chama
   `/match/radar` e posta **card de radar**. Se incompleto, o agente pede o
   que falta (a barra de status mostra o gap).
5. Conversa continua: explorar a base, refinar perfil, expandir matches.

### F2 — Turno de conversa (motor híbrido, todo turno)
1. Front envia `{message, history, profile_atual}` ao endpoint de turno.
2. Agente responde sobre a base (reusa o `explore` do KG + tools do agente) e,
   **se** detectou informação nova de perfil, devolve um `profile_diff`
   estruturado junto da resposta.
3. Front renderiza resposta + card de diff (se houver). **Só o aceite do
   usuário** aplica o diff e dispara `/match/radar`. O agente nunca aplica
   perfil nem roda match — devolve proposta.
4. Após cada radar, o agente ganha no contexto do turno seguinte um resumo do
   ranking (para comentar resultados: "o 1º casa com seu TRL 6 porque…").

### F3 — Anexos e URL
- **URL do site**: livre para anônimo (`/profile/extract` é stateless).
- **Anexo (PDF/deck)**: clique no 📎 quando anônimo → card de gate. Logado:
  upload → extração → card de diff normal. O arquivo entra na `content_library`
  do workspace (reuso do fluxo atual de library + `enrich_content_task`).

### F4 — Expansão de match (D6)
- Clique no item do card de radar → card de expansão inline (dados públicos do
  catálogo via `GET /editais/{id}` + razões do match que o radar já retornou).
- **Anônimo** vê tudo isso. Ações que persistem (brief → `application_log`,
  proposta → `writing_sessions`) exigem conta — o botão existe e mostra o gate.

### F5 — Conversão (anônimo → conta)
1. Trigger: gate (anexo/brief/escrita), ou banner discreto na barra de status
   quando o perfil passa de ~60% ("crie conta para não perder seu perfil").
2. Signup/login → ao voltar, front faz merge do perfil local em
   `PUT /me/profile` e reidrata a conversa (history local). Nada se perde.
3. Conflito (conta já tinha perfil): card de diff "perfil da conta vs. desta
   conversa" — usuário escolhe campo a campo (mesmo componente do D5).

### F6 — Usuário logado retornando
- Cai em `/` com perfil do workspace carregado; barra de status já preenchida.
- Primeira mensagem do agente é contextual: "desde sua última visita entraram
  N fontes; quer ver o radar atualizado?" (usa o ranking persistido/fresh).
- Histórico de conversa do front-door **não** é persistido na v1 (stateless
  como o `/kg-explore` atual); sessões de escrita continuam em `/sessions`.

## 5. Contratos com o backend

### Já existe (reuso direto)
| Peça | Onde | Nota |
|---|---|---|
| Ranking unificado | `POST /match/radar` (`backend/routers/matching.py`) | perfil já vai no body; **hoje exige JWT** só p/ pesos por workspace → delta B2 |
| Chat exploratório público | `POST /kg-explore` (`backend/routers/graph.py`) | stateless, history no cliente, agente via `AGENT_EXPLORE_DEFAULT_ENABLED` — é o esqueleto do motor |
| Extração de perfil por URL | `POST /profile/extract` | público, stateless |
| Extração por documento | `POST /profile/extract/document` | existe; **ganha auth** (gate D3) |
| Detalhe público de item | `GET /editais/{id}`, `GET /graph` | alimenta expansão inline |
| Persistência de perfil | `PUT /me/profile` | merge no signup (F5) |
| Brief GO/NO-GO | `POST /opportunity/brief` | logado; persiste `application_log` — coerente com o gate |
| Rate-limit per-IP | `backend/rate_limit.py` (slowapi) | cobre endpoints públicos |

### Deltas de backend (pequenos, nenhum bloqueante de design)
- **B1 — `POST /frontdoor/turn` (público, rate-limited).** Evolução do
  `kg_explore`: entrada `{message, history, profile}`; saída
  `{answer, profile_diff | null}`. O agente ganha o perfil parcial no contexto
  e uma instrução/tool de **propor** diff (reusa os prompts do
  `profile_extractor`). Não roda match (D4).
- **B2 — `/match/radar` com auth opcional.** Sem JWT → pesos globais default
  (sem `workspace_id`). Perfil já vem no body, então o delta é só tornar o
  `CurrentUserId` opcional nessa rota.
- **B3 — auth no `/profile/extract/document`** (hoje aberto na assinatura).
- **B4 — streaming SSE no `/frontdoor/turn`** (percepção de latência num
  produto chat-first). Desejável; v1 pode lançar sem, com indicador de
  "digitando".
- **B5 — tier de modelo para anônimo.** Turno anônimo roda no tier barato
  (mecanismo de `model_tier` já existe no `/commands`/brief); logado pode
  subir. Controle de custo da porta pública junto com slowapi.

### Estado no cliente (anônimo)
`localStorage`: `frontdoor_profile` (CompanyProfile parcial),
`frontdoor_history` (transcript), `frontdoor_last_radar` (p/ âncora da barra).
Limpar via "começar de novo" no menu. Logado: perfil vem do workspace;
history continua client-side na v1.

## 6. Rotas e aposentadorias (D7)

| Rota | Destino |
|---|---|
| `/` | **front-door** (novo) |
| `/onboarding`, `/matching`, `/dashboard`, `/chat` | redirect → `/` (código removido após estabilizar) |
| `/editais`, `/editais/{id}` | mantém (alvo do "ver página completa") |
| `/library`, `/sessions`, `/pipeline`, `/settings` | mantém, navegação secundária (menu do avatar) até a 1b |

## 7. Não-escopo da 1a

- **1b** (workspace de escrita estilo IDE) — só a fronteira: "começar
  proposta" leva ao fluxo atual de sessions até a 1b existir.
- Persistir o transcript do front-door entre visitas (avaliar na v1.1 com
  dado de uso).
- Multi-empresa / múltiplos perfis por conta.
- Notificações ("novo edital casou com seu perfil") — pós-launch, depende da
  torneira (Fase 1 operação).

## 8. Plano de entrega (marcos)

1. **M1 — esqueleto**: rota `/` com transcript + composer (reuso de
   `components/chat`), estado vazio, `kg-explore` como motor provisório.
2. **M2 — perfil na conversa**: B1 (`/frontdoor/turn` com `profile_diff`),
   card de diff, barra de status, perfil em localStorage.
3. **M3 — radar inline**: B2 (radar público), card de radar, expansão inline
   (D6) com detalhe público + razões.
4. **M4 — gates e conversão**: B3, cards de gate, merge de perfil no signup
   (F5), F6 para logado; aposentar rotas antigas (redirects).
5. **M5 — polish**: B4 (streaming), B5 (tiers), chips contextuais, telemetria
   de conversão (quantos anônimos → conta, em qual gate).

Cada marco é mergeável e demonstrável por si; M1–M3 já entregam o valor
central (conversa → perfil → ranking) sem tocar em auth.

## 9. Riscos e pontos de atenção

- **Custo da porta pública**: LLM por turno anônimo. Mitigação: B5 (tier
  barato) + slowapi por IP + cap de turnos por sessão anônima (ex.: 20/dia,
  card de gate suave ao estourar).
- **Qualidade do diff de perfil**: extração errada irrita mais que formulário.
  O card editável (D5) é a válvula; medir taxa de edição vs. aceite direto.
- **Scroll do chat puro**: a barra de status é a mitigação; se na prática o
  usuário se perder, o plano B já decidido como evolução natural é o painel
  lateral (layout B descartado na v1, não para sempre).
- **Radar sem workspace (B2)**: garantir que pesos default reproduzem o
  comportamento do eval (`matching`/`investor_match`) — rodar as suítes após
  o delta.
