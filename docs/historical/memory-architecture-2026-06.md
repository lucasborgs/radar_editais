# Arquitetura de memória — backend Radar de Editais

São **6 camadas**, cada uma com semântica e granularidade próprias. Não existe um "memory store" único — a memória é decomposta por função (identidade / curadoria / episódica / semântica / síntese / outcomes).

## 1. Identidade — `workspaces.profile` (JSONB)

| | |
|---|---|
| **O que guarda** | [CompanyProfile](../../domain/user_profile.py) estruturada (nome, tipo_entidade, TRL, descricao_atividades, etc.) |
| **Granularidade** | 1 row por workspace |
| **Trigger de escrita** | `PUT /me/profile` (manual) **ou** `POST /profile/extract` (LLM extrai do site via [ProfileExtractor.extract](../../core/ingestion/profile_extractor.py#L70)) |
| **Trigger de leitura** | Toda construção de [WritingSession](../../core/services/writing_session.py#L154) → `profile.to_context()` vira prefixo cacheable do prompt |

## 2. Curadoria — `content_items` (Content Library)

| | |
|---|---|
| **O que guarda** | Upload do usuário: `content` cru + `summary`, `key_facts[]`, `themes[]`, `importance_score` (1–10), `embedding` vector(1536), `last_referenced_at`, `archived_at` (soft-delete) |
| **Trigger síncrono** | `POST /library` → [create_item](../../core/services/content_library.py#L166) faz INSERT imediato (campos enriched vazios) |
| **Trigger assíncrono** | Mesma chamada enfileira procrastinate: [enrich_content_task](../../core/tasks.py#L71) → ao concluir, encadeia [embed_content_task](../../core/tasks.py#L127) (chained via `app.configure_task(...).defer_async`) |
| **Re-enrichment** | `PUT /library/{id}` com `content` alterado → [update_item](../../core/services/content_library.py#L212) chama `enrich_content` **síncrono** (sem encadear embed — gotcha) |
| **Decay temporal** | [mark_items_referenced](../../core/services/content_library.py#L243) atualiza `last_referenced_at` quando o item é injetado (anexo explícito em `library_item_ids` ou `@uuid` mention) — alimenta a fórmula de retrieval (ADR B4) |
| **Trigger de leitura** | (a) anexo direto em `/writing/start` ou `/writing/turn`; (b) @-mention no input; (c) **retrieval automático por turno** via [retrieve_library_items](../../core/retrieval/retriever.py#L430) — scoring `α·recency + β·importance·decay + γ·relevance` |

## 3. Episódica — `writing_sessions` + `session_turns`

| | |
|---|---|
| **Header** | `writing_sessions` (id, workspace_id, edital_id, status, `summary` comprimido, `proposal_outline` JSONB, `section_drafts` JSONB) |
| **Turnos** | `session_turns` (1 row por mensagem, role ∈ user/assistant, `turn_index`, `section_hint`) |
| **Trigger de criação** | `POST /writing/start` → [_create_in_db](../../core/services/writing_session.py#L271). Side effect: linka a [application_log](../../core/services/writing_session.py#L294) existente e avança status para `proposta_iniciada` |
| **Trigger de escrita por turno** | `POST /writing/turn` → [_persist_turn](../../core/services/writing_session.py#L624) insere par user/assistant; se houver `<draft>…</draft>` na resposta, [set_section_content](../../core/services/writing_session.py#L714) atualiza `section_drafts` JSONB |
| **Compressão automática** | Após `COMPRESS_THRESHOLD=10` turnos, [_compress_history](../../core/services/writing_session.py#L884) resume os mais antigos via LLM e persiste em `writing_sessions.summary`. Janela viva mantém `HISTORY_WINDOW=6` |
| **Stateless reload** | Toda construção de WritingSession re-popula histórico do Postgres ([_load_from_db](../../core/services/writing_session.py#L337)) — sobrevive a restart/deploy multi-instância |

## 4. Semântica/RAG do edital — `edital_chunks` (pgvector + tsvector)

| | |
|---|---|
| **O que guarda** | Chunks estruturados por Art./§ ([chunker](../../core/retrieval/chunker.py)) + `embedding` vector(1536) + `text_search` tsvector PT-BR |
| **Trigger principal** | Cron diário 03:00 UTC: [run_daily_etl_task](../../core/tasks.py#L366) roda scrapers e enfileira [chunk_edital_task](../../core/tasks.py#L238) para cada edital novo |
| **Trigger manual** | `scripts/reindex_edital.py` enfileira a mesma task |
| **Idempotência** | [chunk_edital_task](../../core/tasks.py#L260) faz `DELETE WHERE edital_id=…` antes do INSERT — re-rodar sobrescreve limpo |
| **Trigger de leitura** | Cada turno de WritingSession chama [retrieve_chunks](../../core/retrieval/retriever.py#L169): RRF híbrido (dense `<=>` + FTS `to_tsquery`), com boost de 1.5 no edital primário e dedup `max_per_source=2` |
| **Escopo expandido** | [_resolve_edital_scope](../../core/services/writing_session.py#L234) usa KG para incluir até 3 editais análogos no retrieval |

## 5. Síntese — `reflection_insights`

| | |
|---|---|
| **O que guarda** | Insights de 2 níveis: (1) observações factuais com `evidence_ids`; (2) padrões interpretativos. Inclui `outcomes_window_start/end`, `active`, opcionalmente `weight_suggestions` |
| **Trigger** | [reflect_workspace_task](../../core/tasks.py#L177) — hoje **on-demand** (`POST /me/reflect`); plano (ADR §4.3): a cada 5 outcomes acumulados em `application_log` |
| **Guard-rail** | [reflect_workspace](../../core/reflection_service.py#L123) pula se `outcomes < MIN_OUTCOMES_FOR_REFLECTION=5`; weight_suggestions são apenas **logadas** (nunca aplicadas automaticamente) — coerente com a filosofia Grantable |
| **Trigger de leitura** | [_build_reflection_context](../../core/services/writing_session.py#L990) injeta até 6 insights ativos no prompt da WritingSession (prioriza nível 2) |

## 6. Outcomes — `application_log` + `application_events`

| | |
|---|---|
| **O que guarda** | Estado da aplicação por edital (`matched` → `brief_gerado` → `proposta_iniciada` → `submetida` → `aprovada`/`reprovada`), `match_score`, `match_dimensions`, `feedback_notas`, link para `session_id` |
| **Trigger de escrita** | (a) OpportunityBrief cria com `brief_gerado`; (b) [_link_application_log](../../core/services/writing_session.py#L294) na criação de WritingSession; (c) `PUT /applications/{id}/status` manual |
| **Trigger DB** | Trigger `log_application_event` (em migration 004) escreve cada transição em `application_events` — audit trail imutável |
| **Trigger de leitura** | Consumido pelo [reflection_service](../../core/reflection_service.py#L95) para gerar insights |

---

## Fluxo end-to-end de gatilhos assíncronos

```
upload library item ──► INSERT content_items (síncrono, vazio)
                    └─► defer_async enrich_content
                         └─► UPDATE summary/key_facts/themes/importance
                         └─► defer_async embed_content
                              └─► UPDATE embedding (1536d)

cron 03:00 UTC ──► run_daily_etl
                └─► scrapers (cada fonte ativa)
                └─► defer_async chunk_edital (por edital novo)
                     └─► adapter → structurer → chunker → embedder
                     └─► DELETE + INSERT batch em edital_chunks

POST /writing/turn ──► (síncrono no request)
                   └─► embed_query(user_message)  [1x, reusado]
                   └─► retrieve_chunks (edital + análogos)
                   └─► retrieve_library_items (auto)
                   └─► resolve @mentions + mark_items_referenced
                   └─► LLM call
                   └─► INSERT 2 rows em session_turns
                   └─► UPDATE section_drafts (se <draft>)
                   └─► UPDATE summary (se COMPRESS_THRESHOLD)

POST /me/reflect (ou cron futuro) ──► reflect_workspace_task
                                  └─► load outcomes (application_log)
                                  └─► LLM → observations + patterns
                                  └─► INSERT reflection_insights
```

## Observações de design

- **Stateless por request.** Toda WritingSession é reconstruída do Postgres a cada turno; o "estado" vive no DB, não em memória de processo. Coerente com deploy multi-instância.
- **Prompt prefix imutável.** Profile + library anexada + reflection insights + summary comprimido vêm primeiro no prompt — maximiza cache hit (gpt-4o-mini auto / Gemini context cache). RAG e @mentions ficam **depois** do prefixo estável.
- **Decay alimentado só por sinal humano.** `mark_items_referenced` só dispara em anexo direto ou @mention — *não* em retrieval automático, para evitar loop de auto-reforço (decisão explícita em [_build_retrieved_library_context](../../core/services/writing_session.py#L925)).
- **Sem auto-aplicação de aprendizado.** `weight_suggestions` do reflection são logadas mas nunca aplicadas a `matching_weights` automaticamente — humano decide.

---

# Dinâmica — módulos × ações × decisão

A seção anterior cobre o **estado**. Esta cobre a **operação**: como os módulos *agem* e *decidem*. Vocabulário baseado em CoALA (Sumers et al., 2024) e Generative Agents (Park et al., 2023).

## Matriz: módulo × espaço de ações

| Módulo | Grounding (entrada externa) | Recuperação (LTM → working) | Reflexão (LLM transforma) | Aprendizado (working → LTM) |
|---|---|---|---|---|
| **Identidade** | `ProfileExtractor` faz HTTP+LLM 1× | `profile.to_context()` em todo prefixo de prompt | — *(estática, sem síntese local)* | `PUT /me/profile` manual ou extract 1× |
| **Curadoria** | Upload PDF/texto via `POST /library` | 3 modos: anexo explícito • `@uuid` mention • retrieval auto (α·β·γ) | `enrich_content_task` extrai summary/key_facts/themes/importance | INSERT + `embed_content_task` + `mark_items_referenced` (decay) |
| **Episódica** | `user_message` em `turn()` | `_load_from_db` reidrata histórico + summary | `_compress_history` resume turnos antigos quando N>10 | `_persist_turn` (2 rows) + `set_section_content` opcional |
| **Semântica** | Scrapers diários (cron 03:00 UTC) | `retrieve_chunks` (RRF dense+FTS, boost 1.5 primário) | `structurer` (LLM) extrai blocos por Art./§ no silver | DELETE+INSERT idempotente em `edital_chunks` |
| **Síntese** | `_load_outcomes` lê `application_log` | `load_active_insights` (até 6, prioriza level 2) | `reflect_workspace` gera observações (L1) + padrões (L2) | INSERT em `reflection_insights`; weight_suggestions **logadas, não aplicadas** |
| **Outcomes** | `PUT /applications/{id}/status` manual + side-effect do brief | Filtros por status terminal | — *(audit trail é registro, não síntese)* | UPDATE + trigger DB `log_application_event` |

Observações:
- **A única forma de aprendizado totalmente automática é a Semântica** (chunks de editais) — porque é dado público, sem decisão de negócio.
- **Síntese** tem Reflexão pesada mas Aprendizado contido (insights persistem, mas pesos não mudam).
- **Identidade** é a única sem Reflexão — perfil é declaração, não inferência contínua.

## Três loops de decisão

### Loop A — abertura/macro da sessão (humano dirige)

```
Planejamento  → usuário escolhe edital + library_items
Seleção       → outline: wiki page (custo zero) > LLM (fallback) > default hardcoded
              → _resolve_edital_scope: edital primário + até 3 análogos via KG
Execução      → POST /writing/start cria row, linka application_log
```

**Decisão automatizada:** apenas a resolução de escopo (KGMatchService). Resto é humano.

### Loop B — turno (agente dirige, dentro de policy)

```
Planejamento  → implícito no WRITER_SYSTEM (LLM único, sem decomposição)
Seleção       → embed_query 1× → 2 retrievals paralelos:
                  • retrieve_chunks: RRF (0.7 dense, 0.3 FTS), boost primário, max 2/source
                  • retrieve_library_items: α·recency + β·importance·decay + γ·relevance
                Dedup contra anexos + @mentions
                Filtro: relevance_score >= LIBRARY_RELEVANCE_MIN evita ruído
Execução      → 1 chamada LLM → split <draft>/chat → persist 2 rows + section_drafts
              → mark_items_referenced só para sinal humano (anexo/@mention), nunca auto
```

**Decisões automatizadas:** quais chunks/items vão no prompt, se compressão dispara, se há `<draft>` para auto-salvar. **Nunca:** o que escrever no chat — isso é o LLM.

### Loop C — aprendizado longitudinal (batch, supervisionado)

```
Planejamento  → on-demand (POST /me/reflect); futuro: cron a cada 5 outcomes
Seleção       → filtro status ∈ (aprovada, reprovada, submetida)
              → cap MAX_OUTCOMES_PER_REFLECTION=30
              → guard-rail MIN_OUTCOMES_FOR_REFLECTION=5 (pula se insuficiente)
Execução      → LLM → JSON {observations, patterns, weight_suggestions, confidence}
              → INSERT reflection_insights
              → weight_suggestions: log.info() apenas, mesmo com confidence=high
```

**Crítico:** o ciclo *fecha* na injeção do insight no próximo prompt (Loop B), mas **não** na alteração de pesos de matching. O sistema aprende a *contar a história* da empresa para si mesmo, não a *recalibrar suas próprias regras de decisão*.

## Onde o sistema explicitamente **não** decide

| Capacidade ausente | Por quê | Onde |
|---|---|---|
| Auto-extrair facts do chat para a library | Library é curada, não inferida | Só `create_item` via upload |
| Aplicar `weight_suggestions` em `matching_weights` | Política da empresa não muda sem revisão humana | [reflection_service.py:214](../../core/reflection_service.py#L214) |
| Auto-arquivar items de baixa importância | Decay reduz peso no retrieval, mas o item permanece | `archived_at` só via `POST /library/{id}/archive` |
| Auto-transicionar `application_log.status` | Estado da aplicação é fato do mundo, não inferência | Trigger DB só registra; transição vem do PUT |
| Auto-reescrever `section_drafts` sem `<draft>` | Persistência só com sinal explícito do LLM | `_extract_draft` regex em [writing_session.py:90](../../core/services/writing_session.py#L90) |
| Auto-recuperação alimentar decay (`mark_items_referenced`) | Evita loop de auto-reforço | Comentário em [_build_retrieved_library_context](../../core/services/writing_session.py#L939) |

## Tipologia das decisões

Três categorias com fronteira clara:

1. **Decisões de retrieval** (totalmente automatizadas) — qual contexto vai no prompt. Reversíveis turno a turno, sem efeito durável.
2. **Decisões de produção** (LLM dentro de policy) — o que escrever, se há `<draft>`, se comprime histórico. Efeitos duráveis mas escopados à sessão.
3. **Decisões de estado da empresa** (sempre humano) — perfil, items da library, status de aplicação, pesos de matching. Efeitos duráveis cross-session.

Essa estratificação é o que mantém a coerência com a filosofia "AI drafts, humans decide" mesmo conforme o sistema ganha mais capacidades reflexivas.
