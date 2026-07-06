# ROADMAP — Radar de Editais

> Documento **vivo** de direção. Serve de **ponte entre conversas/sessões** (e
> entre modelos): quem retoma o trabalho lê isto primeiro para saber o estado e a
> próxima frente, sem reconstruir o raciocínio. Pendências granulares vivem em
> [BACKLOG.md](BACKLOG.md); o "porquê" arquitetural, nas specs `spec_*.md`.

## Estado atual (2026-06-11)

**Front-door conversacional (Fase 3 · 1a) entregue** via PR #14: `/` é a home
única (chat puro, porta pública) — turno via `POST /frontdoor/turn` (resposta +
diff de perfil proposto), aceite do diff dispara `/match/radar` (agora com auth
opcional), radar inline com expansão/brief/gates de login. Onboarding, matching,
dashboard e /chat-sem-param aposentados por redirect. Detalhe em
[spec_frontdoor_ux.md](spec_frontdoor_ux.md); restos do M5 no BACKLOG.

Antes disso, arquitetura **multi-quadrante** entregue (Fases B+C) e mergeada na `main` via PR #8:
- **Q3 investidor** end-to-end (diretório curado, match-por-tese, `/match/investidores`, card).
- **Fase B surfacing**: `opportunity_type` (edital/desafio/programa) flui scrape→índice→match→badge→pasta do grafo.
- **Escrita `mode=pitch`** (investidor, outbound) + **critic pitch-aware**.
- **Eval** (harness unificado): suítes `investor_match`, `opportunity_type`, gate de não-regressão da extração (0.95).
- **Radar unificado (L2)**: `/match/radar` funde eventos+investidores num ranking — **RRF** (normaliza escalas heterogêneas) + **floor de qualidade** (tier forte/fraco, sem eliminar).

Detalhe e seams em [spec_multi_quadrante.md](spec_multi_quadrante.md) + memória `project_multi_quadrante`.

---

## As 3 iniciativas (sequência recomendada)

> **Reframe-chave:** "profissionalizar/organizar" (#3) **não é uma coisa só** — tem
> três pedaços com timings opostos, e separá-los dissolve o conflito de ordem:
> - **3-apresentação** (README, doc de arquitetura, diagrama, este ROADMAP, limpar branches) → **agora/contínuo** (barato, independe de código, ajuda o handoff).
> - **3-backend** (layering, fronteiras, quebrar `api.py`) → **antes do frontend** (base estável e limpa antes do rewrite).
> - **3-frontend** (estrutura/convenções) → **dentro da #1** (o rewrite é a hora de estruturar o front).

### Ordem

| Fase | O quê | Por que aqui | Pré-requisito / risco |
|---|---|---|---|
| **0 — Handoff** ✅ | merge PR #8 + este ROADMAP + nota de arquitetura + memória | ponte pra próxima conversa/modelo | baixo |
| **1 — Ligar a torneira** ✅ código (PR #9) · 🔄 operação | Descoberta web em prod | fundacional e pequeno | falta só: **shadow-run** diário ~1 semana (runbook spec_dou_feeder §9; dia 1 rodado e logado), revisar 13 labels do golden de triagem (BACKLOG) e setar 4 envs no .env do Docker Compose. Suíte `triage` eval-gated criada (baseline 0.8525/0.9836) |
| **2 — Reorg backend** ✅ (2026-06-11, PRs #10-#13) | api.py→routers; core/ em subpacotes (kg/retrieval/llm/services); README+diagrama; data/ unificado; cache de síntese e ledger duráveis (cloud b+d) | — | frontend intocado (Fase 3); bronze→Storage fica como item solto |
| **3 — Frontend** | **1a ✅ ENTREGUE** (2026-06-11, PR #14: spec + M1–M4 + deltas B1–B3/B5) → **1b workspace tipo IDE** ⬅️ EM IMPLEMENTAÇÃO | base limpa ✅ (Fase 2); dados enriquecem com o shadow-run | spec UX da 1b ✅ (2026-06-11, [spec_workspace_ux.md](spec_workspace_ux.md) — decisões W1–W8 + marcos N1–N4); restos do M5 no BACKLOG; `/chat?edital=` será absorvido pelo workspace (N4) |

### Notas que mudam o cálculo

- **A iniciativa de frontend NÃO depende forte da de dados.** O backend de 1a já existe: extração de perfil a partir de anexos (`core/profile_extractor.py` + upload `core/content_library.py`), ranking unificado (`/match/radar`), chat exploratório (`core/kg_match_service.py` `explore` + agente). A torneira só deixa o radar **mais rico**, não viabiliza. Dá pra antecipar o frontend sobre base menos limpa, se a prioridade for valor visível.
- **Reorg backend e rewrite frontend tocam arquivos diferentes** → poderiam paralelizar com duas frentes; solo, sequencial é mais são.
- **1a substitui onboarding + tela de match; 1b é o workspace pós-seleção** (reaproveita `WritingSession`, inclusive `mode=pitch`). São dois projetos — 1a primeiro (porta de entrada).

### Decisões em aberto (definir antes de cada fase)

- **Fase 1 — DECIDIDO (2026-06-10):** itens `provisorio` são **rotulados**
  (badge "não verificado"), não filtrados; ativação via **shadow-run** local
  primeiro (runbook: spec_dou_feeder §9). O "fix `titulo` vazio" caiu: o
  fallback já existia no código; revalida-se com o dado do shadow-run (BACKLOG).
- **Fase 3 — 1a DECIDIDO (2026-06-11):** stack mantém Next.js 14 in-place; chat
  puro com resultados inline; porta pública (gates: anexo/persistir/escrever);
  motor híbrido (agente propõe diff de perfil, usuário confirma, front dispara
  `/match/radar`). Detalhe em [spec_frontdoor_ux.md](spec_frontdoor_ux.md).
  **Falta:** spec de UX da **1b** (workspace) — specar quando a 1a estiver de pé.

---

## Detalhe da iniciativa de frontend (#1) — esboço

Spec de UX da 1a: ✅ [spec_frontdoor_ux.md](spec_frontdoor_ux.md) (2026-06-11). 1b: a specar.

- **1a — Front-door conversacional.** Interface de chat onde qualquer usuário interage com a base de conhecimento. Elimina onboarding + tela de match: guia o preenchimento do perfil da empresa (aceitando **anexos** → `profile_extractor`/`content_library`) e já devolve as fontes (editais/investidores/programas) em **ranking do match** (`/match/radar`).
- **1b — Workspace tipo IDE (estilo Claude Code).** Após selecionar um item: menu principal numa aba à direita → janela de expansão (explorer de arquivos) → janela de elaboração/edição da proposta → chat com a LLM. Reaproveita `WritingSession`/`/writing/*`.

---

## Refinos já anotados (não bloqueiam; afinar com dado de uso)

Em [BACKLOG.md](BACKLOG.md), seção "Multi-quadrante — follow-ups":
- ranking L2: pesos por quadrante, calibrar `_ENTITY_FLOOR`, urgência como critério de ordenação;
- match tipo-aware de desafio/programa (**bloqueado-por-dados** até a torneira ligar);
- critic de pitch mais rico; botão "escrever pitch" no card Q3.
