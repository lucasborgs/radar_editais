# Spec — Onboarding progressivo e pontos de input do perfil

**Status:** rascunho de design (2026-06-21) · **Owner:** Lucas
**Janela:** pré-lançamento, sem usuários reais — **sem perfis salvos a migrar**.
**Precede:** `mechanism-scope-decisions.md` (mergeado, PR #29) e as decisões de
`project_profile_input_decisions` (diff unificado no `explore_turn`, CNPJ off).
Esta spec é o **próximo passo natural** das pontas de input ainda abertas listadas
naquela memória (§"Pontas de input ainda abertas").

## Por que mexer

O sistema tem **três superfícies de input**, todas alimentando o `CompanyProfile`
([user_profile.py](../../domain/user_profile.py)):

1. **Extract por URL** — `POST /profile/extract`
   ([profile.py:55](../../backend/routers/profile.py#L55)) → `ProfileExtractor`
   legacy (1 fetch + 1 LLM) ou agente
   ([profile_extractor.py:201](../../core/ingestion/profile_extractor.py#L201)).
2. **Conversa do front-door** — `POST /frontdoor/turn`
   (frontdoor.py:69) →
   `KGMatchService.explore_turn` devolve resposta + `profile_updates` numa só
   chamada (kg_match_service.py:758),
   virando um `profile_diff` que o `DiffCard` deixa o humano aceitar/editar.
3. **Edição manual** — `/perfil` ([perfil/page.tsx](../../frontend/src/app/perfil/page.tsx)),
   campos tipados por [profileFields.ts](../../frontend/src/components/frontdoor/profileFields.ts).

**O problema não é falta de mecanismo de coleta — é falta de SEQUÊNCIA.** Hoje a
home (`/`) abre direto no chat conversacional com chips de sugestão; o extract por
URL só existe escondido em `/perfil` (logado). O usuário anônimo cai numa conversa
aberta sem um caminho de menor esforço para "ver valor primeiro". E três dimensões
de match não têm fonte automática nenhuma:

| Dimensão (peso) | Campo do perfil | Fonte hoje |
|---|---|---|
| **mecanismo (15)** | `tipos_financiamento_interesse` | **nenhuma** — só multiselect manual; vazio → nota neutra `w/2` (hybrid_match_service.py:303) |
| **contrapartida (parte)** | `capital_social` | **nenhuma** — só edição manual; órfão |
| **elegibilidade dura** | `uf` · `ano_fundacao` · `faturamento_anual` | extract (uf/ano fracos; faturamento ~nunca no site) |

A tese desta spec (alinhada a `grantable-philosophy`, "AI drafts, humans decide"):
**começar simples; o usuário complementa o perfil DEPOIS que vê valor.** Onboarding
em **2 camadas**.

## Decisões de produto travadas nesta spec (respostas do Lucas, 2026-06-21)

| # | Decisão | Resposta |
|---|---|---|
| 1 | `tipos_financiamento_interesse` — inferir vs perguntar | **Inferir + confirmar.** Função determinística infere candidatos por `porte`+`faturamento`+`trl`+`tipo_entidade`; entram **pré-selecionados** no review da Etapa 1; humano confirma/desmarca. Zero pergunta na Etapa 1. |
| 2 | Etapa 1 — porta de entrada | **Hero de URL antes do chat.** A home vira "Cole o site da sua empresa" → extract → matches. O chat segue acessível, abaixo/depois. |
| 3 | `parceria_ict` no perfil | **Não adicionar agora (backlog).** Mantém só o lado-edital (`find_partners` anexa candidatas ao `/match`); usuário decide caso a caso no card. Evita campo órfão sem consumidor de scoring. |
| 4 | `capital_social` | **Perguntar na Etapa 2 quando relevante** — condicional: só quando o radar mostra edital com contrapartida **E** porte é MEI/ME (onde `capital_social ≥ 500k` vira o jogo, hybrid_match_service.py:337). |

## Princípios invariantes

| Invariante | Regra |
|---|---|
| **AI drafts, humans decide** | Tudo que o sistema infere (mecanismo, etc.) entra como **proposta** no diff/review — nunca escrito silenciosamente. O humano confirma. |
| **Ver valor antes de pedir** | Etapa 1 não faz NENHUMA pergunta. Toda coleta adicional é Etapa 2, disparada por intenção do usuário ("destravar mais matches"). |
| **Não reconstruir coleta** | `explore_turn` (diff inline), `/profile/extract*`, `DiffCard`, `InvestorTrackToggle`, `profileFields.ts` já existem — esta spec **sequencia e provoca**, não reescreve o mecanismo de captura. |
| **Inferência que alimenta scoring → eval-gated** | A inferência de `tipos_financiamento_interesse` muda a dimensão mecanismo → `python -m radar.core.eval matching` antes do merge. O resto (UI/provocação/asks) é input, não scoring → sem eval gate. |
| **Sem migração** | Pré-lançamento; novos campos/inferências não tocam perfis seed. |

---

# O modelo de 2 camadas

```
ETAPA 1 — zero fricção (anônimo, sem perguntas)
  Home = hero "cole a URL da sua empresa"
    → POST /profile/extract  (6 estruturais + uf/ano/trl fracos)
    → infer_financiamento()  (mecanismos candidatos, pré-selecionados)
    → review leve (DiffCard de origem "extract") → aceitar
    → POST /match/radar  → MATCHES NA TELA
  (chat segue disponível abaixo p/ explorar; explore_turn continua captando do texto livre)

ETAPA 2 — assertividade (quando o usuário quer "destravar mais matches")
  CTA gap-driven: pede os 2-3 campos faltantes de MAIOR impacto, em ordem:
    1. confirmar/corrigir tipos_financiamento_interesse (se inferido)
    2. faturamento_anual            (elegibilidade; ~nunca vem do site)
    3. capital_social               (condicional: edital c/ contrapartida + porte MEI/ME)
    4. anexar proposta antiga       (portfolio_projetos + narrativa; /profile/extract-from-document)
    5. trilha de investidor (Q3/Q4) (InvestorTrackToggle, já existe)
```

## Campos por etapa: EXTRAIR vs INFERIR vs PERGUNTAR

| Campo | Dimensão de match | Etapa 1 | Como | Notas |
|---|---|---|---|---|
| `nome` | (id) | ✅ extrair | extract | obrigatório p/ radar |
| `tipo_entidade` | público-alvo | ✅ extrair | extract | confiável no site |
| `one_liner` | tema/setor | ✅ extrair | extract | confiável |
| `solution_summary` | tema/setor | ✅ extrair | extract | confiável |
| `descricao_atividades` | tema/setor | ✅ extrair | extract | obrigatório p/ radar |
| `tamanho_empresa` (porte) | público-alvo + contrapartida | ✅ extrair | extract (fraco) | derivável de nº func./receita; ~50% dos sites |
| `trl` | TRL | ✅ extrair | extract (fraco) | só com evidência clara |
| `uf` | elegibilidade dura | ✅ extrair | extract (fraco) | rodapé/contato; senão Etapa 2 |
| `ano_fundacao` | elegibilidade dura | ⚠️ extrair fraco | extract → Etapa 2 | raramente no site |
| **`tipos_financiamento_interesse`** | **mecanismo (15)** | **🔮 inferir** | `infer_financiamento()` | **pré-selecionado, confirmar na Etapa 1; corrigir na Etapa 2** |
| `faturamento_anual` | elegibilidade dura | ❌ Etapa 2 | perguntar | ~nunca no site; não é dado de CNPJ |
| `capital_social` | contrapartida | ❌ Etapa 2 cond. | perguntar | só se edital c/ contrapartida + porte MEI/ME |
| `portfolio_projetos` | tema/setor (enriquece) | ❌ Etapa 2 | documento | proposta antiga via extract-from-document |
| `estagio`,`mrr_arr`,`round_alvo_brl`,`cap_table_resumo`,`tracao_resumo` | trilha investidor | ❌ opt-in | InvestorTrackToggle | já implementado; revelado pelo switch |

---

# Decisão 1 — Inferir `tipos_financiamento_interesse` (eval-gated)

## Estado atual
- Sem fonte automática. Vazio → `_score_mecanismo` retorna neutro `w/2`
  (hybrid_match_service.py:303).
- Só 2 valores no escopo (PR #29): `subvencao_nao_reembolsavel`,
  `pesquisa_colaborativa` ([profile.ts:3](../../frontend/src/types/profile.ts#L3),
  _MECHANISM_MAP).
- O extract já **retorna** o campo no payload
  ([profile.py:46](../../backend/routers/profile.py#L46)) — hoje sempre vazio.

## Estado-alvo
Função pura `infer_financiamento(profile) -> list[str]` popula o campo no extract,
**antes** de serializar. Entra pré-selecionado no review da Etapa 1 (multiselect já
existe em `profileFields.ts:67`). Humano confirma/desmarca. Como só há 2 opções e
ambas são "fomento competitivo por mérito", a heurística é permissiva e conservadora.

## Heurística (determinística, tunável — pseudo)
```
infer_financiamento(p):
    # Sem sinal técnico → não chuta (mantém neutro w/2; não força um mecanismo).
    tech = p.trl is not None or p.tipo_entidade in {startup, ICT} or termos de P&D/tech em one_liner/descricao
    if not tech: return []

    out = []
    # Subvenção: fomento competitivo p/ empresa com produto/tecnologia. Default amplo.
    if p.tipo_entidade in {empresa, startup}:
        out += [subvencao_nao_reembolsavel]
    # Pesquisa colaborativa: exige ICT; cabe a P&D early-stage / acadêmico / deep tech.
    if p.tipo_entidade in {universidade, ICT} or (p.trl is not None and p.trl <= 4):
        out += [pesquisa_colaborativa]
    return dedup(out) or [subvencao_nao_reembolsavel]  # fallback p/ tech sem desambiguação
```
- Localização: nova função pura em `core/ingestion/profile_inference.py` (ou método estático
  no `ProfileExtractor`). Chamada em `_serialize_extract_result`
  ([profile.py:24](../../backend/routers/profile.py#L24)) — todos os 3 endpoints de
  extract herdam. **Não** roda no `explore_turn` (lá o LLM já decide pelo texto).
- **Por que no serializer e não no `_build_profile`:** mantém `ProfileExtractor`
  focado em "o que o site diz"; inferência é uma camada separada e testável,
  desligável sem tocar a extração.

## Mudanças concretas
| Arquivo | Mudança |
|---|---|
| `core/ingestion/profile_inference.py` (novo) | `infer_financiamento(profile)` puro + testes de tabela |
| `backend/routers/profile.py:24` | chamar a inferência no `_serialize_extract_result` quando o campo vier vazio |
| Frontend (review Etapa 1) | mecanismos inferidos chegam pré-marcados no `DiffCard`/review; copy "inferimos — ajuste se quiser" |

## Gate
**`python -m radar.core.eval matching` OBRIGATÓRIO** — a inferência muda a dimensão
mecanismo para perfis que antes pontuavam neutro. Conferir que p@3/p@5 não regridem
(baseline 2026-06-21: p@3 1.0, p@5 0.9). Os goldens (`iflorestal` etc.) passam a ter
mecanismo inferido — o gate valida que isso não embaralha o top-3.

## Riscos
| Risco | Sev. | Nota |
|---|---|---|
| Inferência errada vira nota cheia onde devia ser neutra | Média | é **proposta** pré-marcada, não final; humano desmarca; gate eval vigia o ranking |
| Heurística cega a nuance (subvenção vs colaborativa) | Baixa | só 2 opções, ambas "mérito"; pior caso = 1 a mais marcado, custo baixo no scoring |

---

# Decisão 2 — Etapa 1: hero de URL na home (sem eval gate)

## Estado atual
- Home (`/`, [page.tsx](../../frontend/src/app/page.tsx)) abre **direto no chat**:
  boas-vindas + `SuggestionChips`. Sem campo de URL.
- Extract por URL só em `/perfil` (logado) via `extractProfileFromUrl`
  ([api.ts:379](../../frontend/src/lib/api.ts)).
- `isRadarReady` = `nome` + `descricao_atividades`
  ([frontdoor.ts:197](../../frontend/src/types/frontdoor.ts#L197)); radar dispara no
  aceite de diff ([page.tsx](../../frontend/src/app/page.tsx)).

## Estado-alvo
Primeira tela (anônimo, perfil vazio) = **hero "Cole o site da sua empresa"**:
1. Input de URL + botão "Analisar" → `POST /profile/extract` (público, rate-limit
   3/min já existe).
2. Resultado vira um review leve (reusa `DiffCard` com `origin:"extract"`, todos os
   campos não-vazios + mecanismos inferidos pré-marcados).
3. Aceitar → `persistProfile` → `runRadar` → **matches na tela**. Zero pergunta.
4. Abaixo do hero/após o radar: o chat segue disponível ("ou explore o fomento por
   aí" + chips). `explore_turn` continua captando perfil do texto livre como hoje.
- **Escape hatch:** "não tenho site / prefiro descrever" → cai direto no chat atual
  (comportamento de hoje, intocado).
- Com perfil já preenchido (retorno/logado) → pula o hero, vai ao estado de
  chat+radar atual.

## Mudanças concretas
| Camada | Arquivo | Mudança |
|---|---|---|
| UI | `components/frontdoor/UrlHero.tsx` (novo) | input URL + estado loading/erro + review |
| UI | `app/page.tsx` | renderizar `UrlHero` quando `isEmpty && !isRadarReady`; "descrever" colapsa o hero |
| UI | reusar `DiffCard` origin `"extract"` | review dos campos extraídos + mecanismos inferidos |
| (nenhuma no backend) | — | `/profile/extract` já existe e é público |

## Gate
Sem eval gate (UI/entrada). `npx tsc --noEmit` + `npx next lint`
([feedback_dev_build_conflict](../../)). Smoke manual do fluxo anônimo.

## Riscos
| Risco | Sev. | Nota |
|---|---|---|
| Extract fraco (`low_confidence`) frustra na 1ª tela | Média | mostrar campos achados + convite a complementar no chat; nunca bloquear |
| Site sem dados / 404 | Baixa | erro controlado já existe (`error` no `ExtractResult`); cair no chat |

---

# Decisão 3 — Etapa 2: "destravar mais matches" (provocação gap-driven)

Cobre os pontos #3 (capital_social), #5 (provocar uf/ano/porte/faturamento) e #6
(documentos) do briefing. **Sem eval gate** — é coleta de input, não muda scoring.

## Estado atual
- `explore_turn` **capta** uf/ano/porte/faturamento do texto livre, mas o sistema
  **nunca PEDE** — depende do usuário mencionar espontaneamente
  (`project_profile_input_decisions` §"PENDENTE").
- `StatusBar` mostra % de completude + "editar perfil"
  ([page.tsx](../../frontend/src/app/page.tsx)), mas não direciona QUAL campo dá mais
  match.
- `capital_social` órfão; `faturamento_anual` quase nunca extraído.
- Upload de documento já vira diff (Composer 📎 → `/profile/extract-from-document`),
  mas é login-gated e não é apresentado como "enriquecer perfil".

## Estado-alvo
Um afluente **gap-driven determinístico** (não depende do LLM lembrar de perguntar):
após o primeiro radar, um card/CTA **"Destravar mais matches"** lista os 2-3 campos
faltantes de maior impacto, em ordem fixa de alavanca:

1. **Confirmar `tipos_financiamento_interesse`** (se foi inferido) — 1 toque.
2. **`faturamento_anual`** — gateia elegibilidade dura; ~nunca vem do site.
3. **`capital_social`** — **condicional**: só aparece se o radar atual tem ≥1 edital
   com `counterpart_required` **E** `porte ∈ {MEI, ME}` (onde `≥500k` muda o score,
   hybrid_match_service.py:337).
   Fora dessa condição, não pergunta (porte já cobre contrapartida).
4. **Anexar proposta antiga** → `portfolio_projetos` + narrativa
   (`/profile/extract-from-document`, já existe; login-gated → `GateCard`).
5. **Trilha de investidor** (`InvestorTrackToggle`, já existe) — opt-in.

Cada item preenchido → reaplica perfil → re-roda radar → o usuário vê o efeito
("AI drafts, humans decide" + efeito composto).

### A "provocação" (#5) — como o sistema PEDE sem virar formulário
- **Determinístico, não-LLM:** uma função `missing_high_impact(profile, radar) ->
  [{field, prompt, why}]` decide o que pedir, ordenada por peso de dimensão e por
  relevância ao radar atual (condicional do capital_social). Renderizada como
  chips/perguntas curtas no card "Destravar mais matches" — clicar abre o input
  tipado (`profileFields.ts`) inline, não navega.
- **Por que não confiar no `explore_turn` para perguntar:** o LLM já CAPTA do texto
  livre, mas provocar é responsabilidade de produto — um gap-check determinístico é
  transparente e testável (vs. torcer para o prompt lembrar). O `explore_turn` segue
  captando quando o usuário responde no chat; os dois caminhos convergem no mesmo
  `profile_diff`.

## Mudanças concretas
| Camada | Arquivo | Mudança |
|---|---|---|
| Lógica | `frontend/src/types/frontdoor.ts` (ou `lib/`) | `missingHighImpact(profile, radar)` — ordena gaps por impacto + condicional capital_social |
| UI | `components/frontdoor/UnlockCard.tsx` (novo) | card "Destravar mais matches" com chips/inputs inline tipados |
| UI | `app/page.tsx` | renderizar `UnlockCard` após o radar quando há gaps de alto impacto |
| UI | surface do 📎 | rotular "anexar proposta antiga p/ enriquecer perfil" no contexto de Etapa 2 |
| (sem backend novo) | — | extract-from-document e PUT /me/profile já existem |

## Gate
Sem eval gate. `tsc --noEmit` + `next lint`. O `capital_social` condicional é
display/coleta — o `_score_contrapartida` **não muda**, só passa a receber o campo
preenchido com mais frequência.

## Riscos
| Risco | Sev. | Nota |
|---|---|---|
| Card de "destravar" vira nag/interrupção | Média | só aparece com gaps de **alto** impacto; dismissable; nunca bloqueia o radar |
| Condicional do capital_social complexa demais | Baixa | regra simples (1 edital c/ contrapartida + porte MEI/ME); senão, omitir |

---

# Decisão 4 — `parceria_ict`: NÃO adicionar agora (backlog)

Mantém só o lado-edital: `find_partners(edital_id)`
(ict_match.py:93) anexa ICTs candidatas ao `/match`
(complemento da spec `mechanism-scope-decisions` D3). O perfil **não** carrega
"tem/quer parceria ICT?" — o usuário decide caso a caso no card de match.

**Registrar em `docs/BACKLOG.md`:** "perfil `parceria_ict` (lado-empresa da pesquisa
colaborativa) — adicionar quando houver consumidor de scoring/personalização do lado
do radar; hoje o complemento edital-cêntrico (`find_partners`) cobre a necessidade
sem campo órfão." Gatilho de reabertura: quando a Fase 3 do
`project_kg_cross_dim_cycle` (ICT no ranking do radar) sair do backlog.

---

# Ordem de implementação (grafo → PRs)

```
PR 1  infer_financiamento() + wire no extract        ← GATED por radar.core.eval matching
PR 2  Etapa 1: UrlHero na home + review              ← depende de PR 1 (mecanismos pré-marcados)
PR 3  Etapa 2: UnlockCard gap-driven + provocação    ← depende de PR 2 (roda após o radar)
       + capital_social condicional + surface do 📎
(backlog) parceria_ict — item no BACKLOG, sem PR
```

- **PR 1 é o único eval-gated** (toca o input da dimensão mecanismo). PR 2 e PR 3
  são UI/entrada — fora do caminho de scoring.
- PR 2 depende de PR 1 só para os mecanismos chegarem pré-marcados no review (sem PR
  1, o hero funciona mas o campo vem vazio).
- PR 3 depende de PR 2 (o `UnlockCard` roda depois do primeiro radar da Etapa 1).

# Gates consolidados

| Gate | Quando | Cobre |
|---|---|---|
| `python -m radar.core.eval matching` | antes de **PR 1** | `infer_financiamento` muda a dimensão mecanismo; p@3/p@5 não regridem vs. baseline 2026-06-21 (1.0 / 0.9) |
| `npx tsc --noEmit` + `npx next lint` | cada PR de frontend | sem `npm run build` com dev ativo ([feedback_dev_build_conflict](../../)) |
| `pytest` (suíte) | cada PR | regressão-zero; novos testes de `infer_financiamento` |

**Premissa MVP** (`project_mvp_os_models`): eval roda **manual** antes do merge.

# Riscos transversais

| Tema | Síntese |
|---|---|
| **Não reconstruir coleta** | `explore_turn`, `/profile/extract*`, `DiffCard`, `InvestorTrackToggle` já existem — a spec sequencia e provoca |
| **Inferência ≠ verdade** | mecanismo inferido é proposta pré-marcada; humano confirma; eval-gate vigia o ranking |
| **Provocar sem formular** | gap-check determinístico + inputs inline; o chat (`explore_turn`) segue como caminho alternativo de captura |
| **Sem migração** | pré-lançamento; campos/inferências novos não tocam perfis seed |

# Estado / progresso

| Decisão | Spec | Implementação |
|---|---|---|
| 1 Inferir `tipos_financiamento_interesse` | ✅ | ⬜ — PR 1 (função + wire); gate eval matching |
| 2 Etapa 1: hero de URL na home | ✅ | ⬜ — PR 2 (UrlHero + review) |
| 3 Etapa 2: UnlockCard gap-driven + capital_social cond. + docs | ✅ | ⬜ — PR 3 |
| 4 `parceria_ict` | ✅ (diferido) | ⬜ — item no BACKLOG, sem PR |
