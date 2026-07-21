# Spec — Escopo dos mecanismos de fomento (3 decisões)

**Status:** rascunho de design (2026-06-21) · **Owner:** Lucas
**Janela:** pré-lançamento, sem usuários reais — **sem perfis salvos a migrar** (remoção de
enum de financiamento é livre; parsing defensivo de valor legado é opcional).

## Por que mexer

O sistema modela "mecanismos de fomento" em **dois vocabulários que se cruzam no
match**: o lado do edital (`card.mechanism ∈ {subvencao, reembolsavel, investimento,
misto}`, vocab WIKI.md §5.1) e o lado do perfil (`tipos_financiamento_interesse`,
escolhido no onboarding). A ponte é o `_MECHANISM_MAP`
(hybrid_match_service.py:57), que
alimenta a dimensão **mecanismo** (15 de 100 pts no Stage 1 —
`_score_mecanismo`:292).

As 3 decisões (racional completo em `project_mechanism_scope_decisions` na memória)
**reduzem e reclassificam** esse eixo:

1. **Cortar `credito_reembolsavel`** — crédito aprova por análise de crédito bancária,
   não por mérito de proposta → a IA de escrita não alavanca. Backlog v2.
2. **`investimento_direto` → trilha separada de investidor** — público VC é quase
   oposto ao de fomento (equity/tração vs subvenção/mérito/TRL). Infra já existe.
3. **EMBRAPII → entidade ICT/parceira complementar** — não tem edital/portal; o aporte
   não-reembolsável anda preso à ENTIDADE (Unidade credenciada), não a um evento.
   Encaixa no eixo evento-vs-entidade.

**Efeito líquido no eixo selecionável:** sobra `subvencao_nao_reembolsavel` +
`pesquisa_colaborativa` (competitivos por mérito, onde a escrita alavanca). Investidor
vira trilha paralela; EMBRAPII/ICT vira camada de parceria sobre o match.

## Achados empíricos (KG ao vivo, 2026-06-21) — fundamentam as decisões

| Fato | Valor | Implicação |
|---|---|---|
| Editais por `mechanism` | subvencao 13 · **investimento 5** · misto 0 · **reembolsavel 0** · (sem) 23 | **D1 não orfana nada** (zero editais de crédito); **D2/D3 deixam 5 editais de `investimento` sem opção que case** |
| `requires_ict_partner=True` | 9 / 41 | gatilho de complemento ICT já populado |
| ICTs no `icts.json` | 90, **todas `kind='embrapii_unit'`/`source='embrapii'`** | flag EMBRAPII é **derivável hoje**; campo explícito é forward-looking (PNIPE etc.) |
| Investidores | 17 (diretório curado) | trilha de investidor já tem substrato |
| Editais de `investimento` | finep:743/760/762/757 (FIPs) + finep:612 (Investimento em Startups) | são **eventos com prazo**, não fundos de VC do diretório |

## Decisões de produto travadas nesta spec (respostas do Lucas, 2026-06-21)

- **D2 — editais de `investimento`:** **surfar na trilha de investidor** (mesmo público
  equity). Continuam editais no `/match`, mas (a) recebem nota **neutra** na dimensão
  mecanismo (não 0) para não afundar, e (b) o radar os agrupa no cluster "capital de
  risco" junto dos investidores.
- **D2 — switch de investidor:** **always-on** — o radar já chama `match_investidores`
  sempre (radar_service.py:202). O switch
  "busca também capital de risco?" **só revela/coleta os campos Q3/Q4** (estagio,
  mrr_arr, round_alvo_brl, cap_table_resumo, tracao_resumo) para melhorar a qualidade do
  match — **não** gateia os resultados.
- **D3 — complemento ICT:** **anexar aos matches de edital** (enriquecer o payload do
  `/match`), edital-cêntrico via `find_partners(edital_id)`. Aditivo, **não muda
  ranking** → não exige o gate de eval `matching`. A inclusão de ICT no **ranking do
  radar** (perfil→ICT) segue diferida no BACKLOG (Fase 3, eval-gated).

## Princípios invariantes (valem nas 3 decisões)

| Invariante | Onde vive | Regra |
|---|---|---|
| **Schema do KG é autoritativo nos docs** | WIKI.md / `wikis/` | Campo/regra novo (flag EMBRAPII) vai no **doc**, não no código; `tests/test_wiki_schema_consistency.py` continua verde |
| **`_MECHANISM_MAP`/ranking → gate de eval** | CLAUDE.md / `core.eval` | Qualquer edit que mude o scoring roda `python -m core.eval matching` antes do merge |
| **ICT é sugestão no match, nunca na escrita** | guard-rail `project_ict_mapping` | O Redator não recebe `find_ict_partners` nem lê `icts.json`; complemento é display + decisão humana |
| **Aditivo e isolado onde der** | de-risk multi-quadrante | Trilha de investidor e complemento ICT não tocam o caminho de scoring do edital |
| **Sem migração de perfil** | pré-lançamento | Remoção de enum é livre; valor legado num perfil seed só deixa de casar (inócuo) |

## Ordem de implementação (grafo de dependências → PRs)

```
PR 1  Eixo de mecanismos (GATED por eval matching)   ← funda; cobre D1 + scoring de D2 + map de D3
   ├── PR 2  Trilha de investidor (UI, sem gate)      ← depende só do enum limpo do PR 1
   └── PR 3  EMBRAPII como ICT + complemento (sem eval gate; validator gate)
```

**Por que o `_MECHANISM_MAP` vira UM PR e não três:** as remoções de
`credito_reembolsavel` (D1), `investimento_direto` (D2) e `matching_embrapii` (D3)
tocam todas o mesmo dict + a mesma dimensão de scoring. `investimento_direto` **e**
`matching_embrapii` são os **únicos** que mapeiam para `investimento` — o tratamento
neutro dos 5 editais FIP precisa entrar **junto** com a segunda remoção, senão há uma
janela em que eles tiram 0. Batê-las num PR único, atrás de um gate de eval só, é mais
seguro que três PRs gated em sequência. PR 2 e PR 3 (display/UI/schema) ficam fora do
caminho de scoring e podem ir depois, em paralelo.

---

# Decisão 1 — Cortar `credito_reembolsavel` (backlog v2)

## Estado atual
- `_MECHANISM_MAP["credito_reembolsavel"] = {"reembolsavel", "misto"}`
  (hybrid_match_service.py:59).
- UI: `FINANCIAMENTO_OPTIONS` em
  [profileFields.ts:23](../../frontend/src/components/frontdoor/profileFields.ts#L23).
- Tipo: union `TipoFinanciamento` em [profile.ts:5](../../frontend/src/types/profile.ts#L5).
- Domínio: comentário de valores em
  [user_profile.py:48](../../domain/user_profile.py#L48).
- Extrator: enum de doc em
  [profile_extractor.py:162](../../core/ingestion/profile_extractor.py#L162) (string livre, sem enum
  duro no backend — `backend/common.py:48` é `list[str]`).
- **KG: 0 editais `reembolsavel`, 0 `misto`** → a entrada do map é dead-weight no corpus atual.

## Estado-alvo
`credito_reembolsavel` sai de todos os 5 pontos acima. Eixo selecionável perde uma opção.
Caminho de volta documentado para v2.

## Mudanças concretas
| Arquivo | Mudança |
|---|---|
| `hybrid_match_service.py:59` | remove a entrada do `_MECHANISM_MAP` |
| `profileFields.ts:23` | remove `{ value: "credito_reembolsavel", ... }` |
| `profile.ts:5` | remove `\| "credito_reembolsavel"` da union |
| `user_profile.py:48` | atualiza o comentário de valores |
| `profile_extractor.py:162` (doc do prompt) | remove a menção (impede o LLM de re-extrair o valor) |

## Caminho de volta (v2)
Quando um edital de crédito entrar no KG (FINEP crédito, BNDES) **e** o produto decidir
cobrir scale-up de indústria: re-adicionar 1 linha ao `_MECHANISM_MAP`, 1 opção na UI e 1
membro na union. Custo trivial (~3 linhas). Registrar item em `docs/BACKLOG.md` ("v2:
crédito reembolsável — reativar quando houver fonte de crédito no KG").

## Gate
`python -m core.eval matching` (convenção CLAUDE.md — toca `_MECHANISM_MAP`). Risco
analítico nulo no corpus atual (sem editais `reembolsavel`/`misto`), mas o gate é
mandatório por regra.

## Riscos / abertas
- **Baixo.** Único risco é um perfil seed com `credito_reembolsavel` salvo deixar de
  casar — inócuo (não há editais de crédito).

---

# Decisão 2 — `investimento_direto` → trilha separada de investidor

## Estado atual
- `_MECHANISM_MAP["investimento_direto"] = {"investimento", "misto"}`
  (:60) — **está no map mas NÃO na UI nem
  na union** (`profileFields.ts`/`profile.ts` não o listam). Resíduo de scoring sem entrada
  de seleção.
- Infra de investidor **já existe e já está ligada**:
  - `match_investidores(profile, top_k)`
    (investor_match.py:112) — match-por-tese,
    sem gate.
  - `POST /match/investidores` ([matching.py:53](../../core/eval/matching.py#L53)).
  - O radar **já inclui investidores incondicionalmente**
    (radar_service.py:202); `_entity_item`
    normaliza (:76).
  - Campos Q3/Q4 no perfil: `estagio`, `mrr_arr`, `round_alvo_brl`, `cap_table_resumo`,
    `tracao_resumo` ([user_profile.py:54-58](../../domain/user_profile.py#L54);
    backend `common.py:52`; extrator `profile_extractor.py:140`).
  - Frontend renderiza investidor no radar (RadarCard.tsx, `opportunity_type="investidor"`, botão `onPitch`).
- **5 editais `mechanism='investimento'`** (FIPs + Investimento em Startups) hoje casam só
  via `investimento_direto`/`matching_embrapii`.

## Estado-alvo
- `investimento_direto` sai do `_MECHANISM_MAP` (eixo de fomento não o conhece mais).
- Os 5 editais de `investimento` **não afundam**: nota neutra na dimensão mecanismo +
  agrupamento no cluster "capital de risco" do radar.
- Switch opt-in **"busca também capital de risco?"** revela os campos Q3/Q4 (não gateia
  resultados — investidores seguem always-on no radar).

## Mudanças concretas
| Camada | Arquivo | Mudança |
|---|---|---|
| Scoring | `hybrid_match_service.py:60` | remove `investimento_direto` do `_MECHANISM_MAP` |
| Scoring | `_score_mecanismo` (:292) | quando `card_mechanism == "investimento"` e nenhuma opção do perfil casa → retornar **neutro** (`w/2`), espelhando a regra "card sem mechanism → neutro" (:298). Evita 0 silencioso nos FIPs |
| Radar (display) | `radar_service.py` (`_event_item`/`merge_radar`) | derivar um rótulo de cluster "capital de risco" para itens com `payload.mechanism == "investimento"`, ao lado de `kind_class="entidade"` (investidor). **Não muda score/RRF** — é agrupamento de display |
| UI | novo controle no front-door/perfil | switch "busca também capital de risco?"; ON → mostra os inputs Q3/Q4 (specs já existem em `profileFields.ts:47-68`); estado persistido no perfil (campo derivado ou flag local) |
| UI | `RadarCard.tsx` | renderizar o cluster "capital de risco" (investidores + editais de investimento) sob um cabeçalho próprio |

**Nota:** `investimento_direto` já não está na union/UI, então não há remoção de enum aqui
(diferente de D1/D3). A mudança de seleção é só **aditiva** (o switch + os campos Q3/Q4 que
já existem no schema).

## Ordem / PRs
- Parte de scoring (remoção do map + neutro) entra no **PR 1** (gated).
- Switch + cluster de display = **PR 2** (sem eval gate; é UI/agrupamento).

## Gate
A parte de scoring está no PR 1 → `python -m core.eval matching` **obrigatório** (impacto
real nos 5 FIPs: de `investimento`-casado para neutro). Conferir que o ranking dos FIPs
não regride além do esperado (eles devem cair levemente, não sumir).

## Riscos / abertas
| Risco / aberta | Sev. | Nota |
|---|---|---|
| Tratamento "neutro" pode mascarar editais de investimento mal-classificados | Baixa | `investimento` é mechanism raro e curado; aceitável |
| Como o radar agrupa display ("capital de risco" como seção vs badge) | aberta | detalhe de frontend; decidir no PR 2 |
| Switch persiste onde? (flag no perfil vs estado de UI) | aberta | sugestão: flag derivada — se qualquer campo Q3/Q4 preenchido, trilha ON; switch é atalho |
| Reusar match: **não reconstruir** o investor match | — | só conectar a entrada (já ligado no radar) — guard explícito |

---

# Decisão 3 — EMBRAPII como entidade ICT / parceira complementar

## Estado atual
- `_MECHANISM_MAP["matching_embrapii"] = {"investimento", "misto"}`
  (:62); opção `matching_embrapii` **está
  na UI e na union** (`profileFields.ts:24`, `profile.ts:6`, comentário em
  `user_profile.py:49`, doc do extrator).
- Arquitetura ICT já meio-construída (`project_ict_mapping`):
  - Nó `ict` no KG; `ict_schema()` em WIKI.md §6.1.2
    (wiki_schema.py:259).
  - `core/ict_match.py::find_partners(edital_id)`
    (:93) — ranking determinístico por overlap de tema.
  - `requires_ict_partner` (campo derivado da entry, WIKI.md §5.10) +
    `ict_requirement_patterns()` (wiki_schema.py:280).
  - Tool `find_ict_partners` no Explorador
    ([explore_tools.py:260](../../core/llm/agent_tools/explore_tools.py#L260)).
- **ICTs só afloram no chat de explore** — nunca no `/match` nem no radar.
- **90 ICTs, todas `embrapii_unit`** → "trazer co-financiamento" é hoje universal no
  artefato, mas o campo que **representa** isso não existe.

## Estado-alvo
- (a) `matching_embrapii` sai do eixo selecionável (UI/union/map/extrator).
- (b) Unidade EMBRAPII modelada como ICT com atributo explícito de **co-financiamento**.
- (c) ICTs afloram como **complemento ao match de edital** (não no ranking), com selo
  "este parceiro pode trazer co-financiamento" quando o edital exige/se beneficia de
  parceiro.
- (d) Generalizado para `pesquisa_colaborativa` (mesma forma: edital exige ICT) — que
  **permanece** opção selecionável.

## Mudanças concretas

### (a) Remover `matching_embrapii` do eixo
| Arquivo | Mudança |
|---|---|
| `hybrid_match_service.py:62` | remove a entrada do `_MECHANISM_MAP` (parte do PR 1, gated) |
| `profileFields.ts:24` | remove `{ value: "matching_embrapii", ... }` |
| `profile.ts:6` | remove `\| "matching_embrapii"` da union |
| `user_profile.py:49` | atualiza comentário |
| `profile_extractor.py` (doc do prompt) | remove menção |

### (b) Flag de co-financiamento no schema ICT (WIKI.md — autoritativo)
- **WIKI.md §6.1.2 / `ict_schema`:** adicionar `brings_cofinancing` a `node_fields`
  (campo **opcional**, NÃO em `required_fields` — senão o validador exige em todas as 90
  ICTs antes de regenerar). Documentar a semântica: "ICT cujo arranjo aporta recurso
  não-reembolsável ao projeto (Unidade EMBRAPII: ~1/3 do custo). Default `false`; derivado
  `true` para `source=='embrapii'`."
- **`build_ict_graph.py`** (gerador de `icts.json`): popular `brings_cofinancing`
  (derivado de `source=='embrapii'` hoje; curável por fonte no futuro). Regenerar
  `icts.json`.
- **Validador** `test_wiki_schema_consistency.py`:
  - `test_ict_nodes_have_required_fields` checa `keys − node_fields == ∅` → o campo
    **precisa** estar em `node_fields` (senão o `icts.json` regenerado falha como
    "campo fora do schema").
  - Não adicionar a `required_fields` → ICTs antigas sem o campo continuam válidas.
  - **Por que não overload em `kind`:** `kind` já distingue `embrapii_unit`; mas
    co-financiamento é fato econômico do *arranjo*, não do tipo de instituição (um lab
    PNIPE não traz). Campo semântico próprio é mais limpo e generaliza.

### (c) Complemento ICT no `/match` (aditivo, sem eval gate)
- **Backend:** enriquecer cada resultado de `HybridMatchService.match` com
  `ict_partners` (lista de `PartnerSuggestion` via `find_partners(edital_id)`) **quando**
  `requires_ict_partner` OU houver overlap temático relevante. Marcar `brings_cofinancing`
  por parceiro → habilita o selo. Pode ser um passo de pós-processamento no service ou no
  router `/match`, mantendo `find_partners` puro.
- **Frontend:** renderizar parceiros ICT + selo "pode trazer co-financiamento" no card de
  match (`RadarCard.tsx` / card de edital).
- **Guard-rail (não violar):** ICT é sugestão **no match**, não na escrita. O Redator
  segue sem `find_ict_partners`/`icts.json` (`project_ict_mapping`). "Sugestão ≠
  compromisso."
- **Sem gate de eval `matching`** — é enriquecimento de display, não muda score/ranking.
  Rodar a suíte só como smoke (tocamos o service file).

### (d) Generalizar para `pesquisa_colaborativa`
- `pesquisa_colaborativa` **fica** como opção selecionável (`_MECHANISM_MAP`
  inalterado para ela). O complemento ICT da parte (c) já cobre qualquer edital com
  `requires_ict_partner` — inclusive os de pesquisa colaborativa — sem código extra.
  Documentar que o gatilho é `requires_ict_partner`, ortogonal ao mechanism.

## Ordem / PRs
- (a) parte de map → **PR 1** (gated, junto das outras remoções).
- (a) parte de UI/union/extrator + (b) schema + (c) complemento + (d) doc → **PR 3**
  (sem eval gate; passa pelo validator de schema).

## Gate
- `python -m core.eval matching` cobre a remoção de `matching_embrapii` do map (no PR 1).
- `python tests/test_wiki_schema_consistency.py` (validator) **verde** após adicionar
  `brings_cofinancing` e regenerar `icts.json` (PR 3).
- ICT como **ranking** no radar (perfil→ICT) **fica diferido** (BACKLOG Fase 3,
  eval-gated `project_kg_cross_dim_cycle`) — fora do escopo desta spec.

## Riscos / abertas
| Risco / aberta | Sev. | Nota |
|---|---|---|
| Over-surfacing: anexar ICT a editais demais vira ruído | Média | gatilho conservador (`requires_ict_partner=True`, hoje 9/41); overlap temático puro é opcional e mais ruidoso — decidir o limiar |
| `brings_cofinancing` derivado vs curado | Baixa | hoje 100% derivável de `source=='embrapii'`; vira curável quando PNIPE/outras fontes entrarem |
| Cópia do selo ("pode trazer co-financiamento") sugere garantia | Média | linguagem de possibilidade, não compromisso (alinha guard-rail) |
| Regenerar `icts.json` (gitignored) em quem aplica | Baixa | rodar `build_ict_graph.py` faz parte do PR 3 |

---

# Gates consolidados

| Gate | Quando | Cobre |
|---|---|---|
| `python -m core.eval matching` | antes de mergear **PR 1** | toda edição do `_MECHANISM_MAP` + `_score_mecanismo` (D1 + scoring D2 + map D3); foco: ranking dos 5 FIPs cai levemente, não some |
| `python tests/test_wiki_schema_consistency.py` | antes de mergear **PR 3** | `brings_cofinancing` em `node_fields` + `icts.json` regenerado consistente |
| Suíte completa (`pytest`) | cada PR | regressão-zero; `test_hybrid_match`/`test_ict_match`/`test_radar_service` |

**Premissa MVP** (`project_mvp_os_models`): eval roda **manual** antes do merge (não
queimar OpenAI em CI); com `EMBEDDING_BACKEND` OS quando aplicável.

# Riscos transversais (consolidado)

| Tema | Decisões | Síntese |
|---|---|---|
| **Acoplamento do `_MECHANISM_MAP`** | D1+D2+D3 | as 3 remoções tocam o mesmo dict; `investimento_direto`+`matching_embrapii` são os únicos `investimento` → o tratamento neutro precisa entrar junto. Batidas no PR 1, um gate só |
| **Schema autoritativo nos docs** | D3 | `brings_cofinancing` vai em WIKI.md §6.1.2, não no `.py`; validador é o portão |
| **ICT sugestão, não compromisso** | D3 | complemento no match nunca entra na escrita sem decisão humana |
| **Sem migração** | D1, D3 | pré-lançamento; valor de financiamento legado em perfil seed só deixa de casar (inócuo) |
| **Não reconstruir caminhos existentes** | D2, D3 | investor match e `find_partners` já existem — só conectar a entrada/saída |

# Estado / progresso

| Decisão | Spec | Implementação |
|---|---|---|
| 1 Cortar `credito_reembolsavel` | ✅ | ⬜ — PR 1 (map + UI/union/extrator); gate eval matching |
| 2 `investimento_direto` → trilha investidor | ✅ | ⬜ — scoring no PR 1 (gated); switch + cluster display no PR 2 |
| 3 EMBRAPII → ICT complementar | ✅ | ⬜ — map no PR 1 (gated); UI/schema/complemento no PR 3 (validator gate) |
