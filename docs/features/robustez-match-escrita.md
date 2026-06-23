# Spec — Robustez do Agente de Match + Escrita

> **Objetivo:** tornar o núcleo do produto — recomendar editais vigentes e assistir a escrita de propostas ancorada em contexto validado — robusto e confiável em produção. Quatro frentes, uma sequência.
> **Base:** branch `test-integration` (5 commits sobre o merge ETL+critic). **Data:** 2026-06-02.
> **Fora de escopo (deferido):** memória longitudinal por agência (`agency_insights`). Baixo ROI com dezenas de editais/ano; evolução de ecossistema, não robustez do produto.

## Decisões travadas

| # | Frente | Decisão |
|---|---|---|
| 1 | Path de escrita dark-launched | **Aposentar o legacy** — só existe o path agente |
| 2 | Dois matchers coexistindo | **HybridMatch** é o matcher de produção a validar/endurecer |
| 3 | Consciência temporal ausente | **Injetar `reference_date`** em todos os prompts de match/escrita/critic |
| 4 | Qualidade do RAG de escrita | **Adicionar reranker** após o RRF |

---

## Front 1 — Aposentar o path legacy de escrita

### Problema
Todo o grounding que torna a escrita confiável (critic, coerência interna, tools `search_edital`/`read_section`, `save_draft` com gate) **só roda quando `workspaces.agent_writing_enabled = true`** — e o default é **OFF** ([writing_session.py:838](../core/writing_session.py#L838)). Em produção, a escrita cai no **legacy 1-shot por regex `<draft>`** ([writing_session.py:94](../core/writing_session.py#L94)), **sem critic, sem coerência, sem grounding por tool**. A segurança construída está inerte. Além disso, manter dois paths gera comportamento bimodal e duplica prompts (`WRITER_SYSTEM` vs `WRITER_AGENT_SYSTEM`).

### Design
Remover o path legacy e tornar o agente o único fluxo de escrita.

**Remover** (após verificar uso compartilhado):
- `_turn_legacy` e o branch `if self._use_agent()` no dispatcher `turn()` ([writing_session.py:609,624](../core/writing_session.py#L624)).
- `_split_draft`, `_DRAFT_RE` ([writing_session.py:94-98](../core/writing_session.py#L94)) e a convenção `<draft>`/`[COMPLETAR:]`.
- `WRITER_SYSTEM` legacy e o `_build_messages` legacy ([writing_session.py:128,1103](../core/writing_session.py#L128)). **Atenção:** `OUTLINE_SYSTEM` e `COMPRESS_SYSTEM` podem ser compartilhados (plano/compressão) — confirmar antes de remover.
- Coluna/flag `agent_writing_enabled`: migração SQL para dropar a coluna; remover `_use_agent`, o cache `_use_agent_cached`, e [scripts/agent_rollout.py](../scripts/agent_rollout.py) (parte de writing).
- Env vars `AGENT_WRITING_*` que só serviam o gate.

**Manter como kill-switch técnico:** o `resolve_agent_provider` já dá fallback de provider; o "fallback" agora é de modelo/provider, não de path. Se o agente falhar (`stop_reason == "error"`), retorna erro amigável ([writing_session.py:765](../core/writing_session.py#L765)) — sem cair em legacy.

### Pré-requisito de segurança (gate da remoção)
**Não remover o legacy sem antes ter o eval harness do Front 1.5 verde.** A remoção é irreversível; precisamos de evidência de que o agente entrega ≥ a qualidade do legacy.

### Arquivos
`core/writing_session.py`, `supabase/migrations/*_drop_agent_writing_flag.sql`, `scripts/agent_rollout.py`, `scripts/eval_agent_writing.py`, `tests/test_writing_session_agent.py` (remover asserts de dispatcher legacy).

### Critérios de aceitação
- `turn()` sempre roda o agente; nenhum caminho lê `agent_writing_enabled`.
- Suíte verde sem os testes de legacy; novos testes cobrindo "turn sempre agente".
- Grep por `<draft>`, `_turn_legacy`, `WRITER_SYSTEM`, `agent_writing_enabled` = 0 ocorrências ativas.

---

## Front 1.5 — Eval harness de escrita (habilitador do Front 1)

### Problema
[scripts/eval_agent_writing.py](../scripts/eval_agent_writing.py) é um stub (TODO). Sem métrica, aposentar o legacy é fé, não engenharia. Também precisamos endurecer a confiabilidade do writer no tool-loop (observado: o agente às vezes não chama `save_draft` num turno).

### Design
Harness que, sobre um conjunto fixo de (perfil, edital, instrução de seção), roda o agente e mede — usando a **rúbrica de seção** já definida:
- **nº de afirmações sobre o edital** e **% com respaldo em chunk** (grounding);
- **nº de erros factuais** (rodando o critic como juiz offline);
- **conclusão do save** (o agente persistiu a seção em ≤ N turnos? — confiabilidade do tool-loop);
- **coerência interna** (0 contradições entre seções no doc final).

Conjunto-semente: perfil iFlorestal + `finep:612` + 2-3 outros editais com chunks. Saída: tabela comparativa por caso + agregados.

### Arquivos
`scripts/eval_agent_writing.py`, `tests/fixtures/eval_cases.json`.

### Critérios de aceitação
- Roda com 1 comando, produz métricas reproduzíveis.
- Estabelece o **baseline** que o Front 1 precisa igualar/superar para justificar a remoção do legacy.

---

## Front 2 — Validar e endurecer o HybridMatch

### Problema
Nunca validamos se o `/match` rankeia bem — é o topo do funil. Se erra o edital, todo o resto (brief, escrita) é desperdício. O HybridMatch ([hybrid_match_service.py:542](../core/hybrid_match_service.py#L542)) é Stage 1 determinístico (elegibilidade/TRL/tema/mecanismo/contrapartida) + Stage 2 LLM temático, com pesos em `matching_weights`.

### Design
**(a) Validação com rúbrica.** Rodar `/match` com o perfil iFlorestal (e 1-2 perfis sintéticos contrastantes) e julgar o top-K por edital:
- fit temático 0-2, elegibilidade 0-2, vigência correta sim/não.
Medir precisão@K e se `finep:612` (fit forte) aparece no topo.

**(b) Endurecimentos identificados:**
- **Vigência:** já corrigida no runtime (Front entregue). Garantir que o eval cobre.
- **Fallback "sem elegíveis":** hoje, se ninguém passa o Stage 1, devolve top sem filtro ([hybrid_match_service.py:563](../core/hybrid_match_service.py#L563)) — pode recomendar inelegível. Spec: sinalizar explicitamente "nenhum elegível" em vez de mascarar.
- **Transparência:** garantir que `match_dimensions` explica o score (já existe; validar no eval).
- **KGMatch:** permanece só no `explore` (vitrine). Não é o matcher de produção; não investir agora.

### Arquivos
`core/hybrid_match_service.py`, `scripts/eval_matching.py` (novo), `tests/test_hybrid_match_*.py`.

### Critérios de aceitação
- Harness de eval de matching reproduzível com rúbrica.
- `finep:612` no top-3 para o perfil iFlorestal; nenhum expirado/inelegível no top-K.
- Caso "nenhum elegível" devolve sinal claro, não top mascarado.

---

## Front 3 — Consciência temporal nos prompts

### Problema
`reference_date` existe no `index.json` mas **nunca é injetada em prompt algum** (maior P0 transversal do relatório). Para um produto cujo diferencial é "editais **vigentes**", o texto gerado e o matching não relativizam prazos.

### Design
Injetar um bloco de contexto temporal canônico em todos os prompts relevantes:
```
[CONTEXTO TEMPORAL: hoje é {reference_date}. O edital {id} encerra em {deadline}
({dias_restantes} dias). Se o prazo já passou, avise e não prossiga sem confirmação.]
```
- **Escrita (agente):** no prefixo estável da sessão + instrução "ao mencionar prazos, relativize a hoje".
- **HybridMatch / brief:** injetar `reference_date` no Stage 2 e no brief; instruir a copiar `status`/`deadline` verbatim do catálogo, nunca estimar.
- **Critic:** já consome chunks; adicionar a data de referência para validar afirmações temporais ("o prazo é X") contra o deadline real.

Fonte única: helper `temporal_context(edital_id)` lendo `index.json` + `wiki page`, calculando `dias_restantes`.

### Arquivos
`core/temporal.py` (novo helper), `core/writing_session.py`, `core/hybrid_match_service.py`, `core/opportunity_brief_service.py`, `core/agent_tools/critic_agent.py`.

### Critérios de aceitação
- Todo prompt de match/escrita/brief/critic recebe `reference_date`.
- Teste: rascunho que afirma prazo divergente do deadline real é pego pelo critic.
- Teste: brief de edital com prazo curto destaca a urgência.

---

## Front 4 — Reranker no RAG de escrita

### Problema
`retrieve_chunks` funde dense + FTS via RRF (k=60, `fts_weight=0.3`) e devolve top-k direto ([retriever.py:169](../core/retriever.py#L169)) — **sem reranker**. O critic e o escritor só são tão bons quanto os chunks que chegam; um RRF puro pode rankear mal em queries sutis.

### Design
Inserir um estágio de rerank entre a fusão RRF e o corte top-k:
1. **Over-fetch:** RRF retorna um pool maior (ex.: `k_candidates = 20`).
2. **Rerank:** reordenar o pool por relevância à query.
   - Opção A (default): **cross-encoder** (ex.: modelo de reranking multilíngue) — local, sem custo de API, baixa latência.
   - Opção B: **LLM reranker** (gpt-4o-mini com prompt de scoring) — sem infra nova, mais caro/lento.
   - Spec recomenda **A**, com B como fallback configurável via env (`RERANK_BACKEND`).
3. **Corte:** manter top-k do pool reordenado, preservando o `primary_boost` e o dedup `max_per_source`.

Métrica de validação reusa o harness do Front 1.5 (% de afirmações com respaldo em chunk) + recall@k num conjunto rotulado pequeno.

### Arquivos
`core/retriever.py`, `core/reranker.py` (novo), `pyproject.toml` (dep do cross-encoder), `tests/test_reranker.py`.

### Critérios de aceitação
- `retrieve_chunks` aceita `rerank=True` (default) e degrada graciosamente se o backend falha (volta ao RRF puro).
- Medição mostra ganho em recall@k / precisão dos chunks vs RRF puro no conjunto-semente.
- Latência por turno dentro de orçamento aceitável (medir e reportar).

---

## Cross-cutting

- **Eval primeiro:** Front 1.5 (harness de escrita) e o eval de matching (Front 2) são pré-requisitos — não mexer em produção sem baseline.
- **Rúbrica única** (reusada em todos os evals): por edital — fit temático 0-2, elegibilidade 0-2, vigência sim/não; por seção — nº afirmações, % com respaldo em chunk, nº erros factuais, coerência interna.
- **Provider:** tudo roda no fallback OpenAI (`gpt-4o`) hoje; a spec é provider-agnóstica via `resolve_agent_provider`.

## Sequência sugerida (dependências)

```
Front 1.5 (eval escrita)  ─┐
Front 4   (reranker)       ├─→ melhoram a qualidade medível da escrita
Front 3   (temporal)       ─┘
        │
        ▼
Front 1 (aposentar legacy)  ← só após 1.5 mostrar agente ≥ legacy
Front 2 (validar/endurecer matching)  ← independente, pode correr em paralelo
```

1. **Front 1.5** — harness de eval de escrita (baseline). *Habilita tudo.*
2. **Front 3** — consciência temporal (baixo risco, alto valor transversal).
3. **Front 4** — reranker (eleva a qualidade dos chunks que o critic/escritor veem).
4. **Front 1** — aposentar legacy (gated pelo 1.5).
5. **Front 2** — validar/endurecer matching (paralelizável).

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Aposentar legacy sem rede | Gate pelo eval (Front 1.5); manter erro amigável no `stop_reason=error` |
| Writer (gpt-4o) não chama `save_draft` de forma confiável | Medir no harness; ajustar prompt/`max_steps`; considerar Claude quando houver key |
| Reranker adiciona latência | Over-fetch modesto (20), cross-encoder local, degradação graciosa p/ RRF |
| Reranker dep pesada | Avaliar tamanho do modelo; lazy-load; env para desligar |
| `reference_date` stale (cron não rebuilda índice) | Já mitigado em runtime no match; documentar e abrir item de cron rebuild |

## Questões em aberto

1. **Cron rebuild do índice** (gap P0 do relatório): fora desta spec, mas a consciência temporal depende de `reference_date` fresca. Abrir item separado?
2. **Agentes explore/extractor** também estão atrás de flags — esta spec só aposenta o de escrita. Alinhar depois?
3. **Modelo de reranking** específico (qual cross-encoder multilíngue) — decidir na implementação do Front 4.
