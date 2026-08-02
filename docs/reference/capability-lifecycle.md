# Ciclo de vida das capacidades

**Status:** referência vigente · **Verificado em:** 2026-07-15

Este inventário distingue profundidade técnica de disponibilidade no produto.
Uma capacidade desligada pode ser experimental, dormente ou apenas opcional;
essas categorias não autorizam remoção ou ativação automática.

| Estado | Contrato |
|---|---|
| ativa | participa do runtime padrão e possui consumidor atual |
| opcional | caminho suportado, habilitado por configuração explícita |
| experimental | executável no laboratório/eval, sem contrato de produção |
| dormente | preservada, mas deliberadamente não produz novo estado no runtime atual |
| histórica | registro sem autoridade sobre o runtime |

## Inventário atual

| Capacidade | Estado | Ativação/fallback |
|---|---|---|
| Deep Research na escrita | ativa, degradável | falha de busca/staging não quebra o turno |
| Deep Research no Explore | opcional | `EXPLORE_DEEP_RESEARCH_ENABLED=true`; fallback para tools do catálogo |
| exploração profile-first da Fase 1 no Explorar | opcional | `KG_PHASE1_EXPLORE_ENABLED=true` (default off, read-only); indisponibilidade do grafo é comunicada honestamente, sem fallback silencioso |
| refresh automático da projeção da Fase 1 pós-gold | opcional, operacional | `KG_PHASE1_AUTO_REFRESH_ENABLED=true` (default off); roda `radar.core.kg.phase1.ingest.build()` após o commit do gold no `run_daily_etl` e na promoção de edital — best-effort e idempotente (source_hash); falha nunca quebra a run |
| RAG factual no Explore | ativa | default on; `EXPLORE_FACTUAL_RAG_ENABLED=false` é kill switch emergencial |
| Descoberta DOU, hub e Crawl4AI | opcional, operacional | flags do worker; todo achado permanece em staging |
| reranking | opcional, avaliado | `MATCH_RERANK_ENABLED` + `RERANK_BACKEND`; fallback para ranking base |
| ProfileExtractor agêntico | experimental | default off; promoção exige critério aceito na suíte `profile_extractor` |
| lookup CNPJ/BrasilAPI | experimental subordinada | default off; depende do ProfileExtractor agêntico e de ganho medido |
| embeddings locais `sentence_transformers` | experimental | exige eval, compatibilidade de dimensão e rebuild deliberado |
| leitura de memória do workspace | ativa, degradável | lê insights existentes; Store indisponível cai para leitura estática |
| escrita automática de memória | dormente | `AUTO_MEMORY_WRITE=0`; ativação exige curadoria, TTL e eval anti-contaminação |
| learned overlays cross-workspace | dormente | reader/tabelas preservados, sem writer; ativação exige consentimento, anonimização e revisão humana |

## Invariantes

- uma flag default off não é, sozinha, evidência de código morto;
- capacidade opcional deve possuir consumidor, configuração e fallback;
- capacidade experimental não deve ser apresentada como disponível ao usuário;
- capacidade dormente não escreve novo estado antes de cumprir seu gate; e
- aprendizagem cross-workspace exige consentimento ligado ao processamento real,
  nunca uma preferência antecipada sem consumidor.

Detalhes e evidências da classificação estão na
[`spec de capacidades dormentes`](../specs/dormant-capabilities.md).
