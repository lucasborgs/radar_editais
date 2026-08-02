# Projeção da Fase 1 do grafo (`radar.core.kg.phase1`)

> **Autoridade:** comportamento e contrato do módulo de produção da projeção da
> Fase 1 do grafo (KG-P1A). Deriva do gold — NÃO é uma nova fonte de verdade.
> Relatório de execução: `docs/execution/kg-phase1-production/reports/KG-P1A-projection.md`.

## 1. Propósito

Reprojetar de forma **durável, reconstruível e isolada** a Fase 1 determinística
do property graph validada na spike `kg-structure-aware` (SPEC §8). A projeção
vive no schema próprio `kg_phase1` (migration 048), derivada 100% do gold
(`public.entities` + `public.entity_relationships`) com **zero LLM**, pronta
para consumo futuro pelo Explorar. Explorar, Match, RAG, memória e Fase 2
permanecem intocados.

## 2. O que a projeção materializa

| Conteúdo | Origem | `edges.origin` |
|---|---|---|
| Nós (substâncias) | `entities` (id = `<kind>:<native_id>`) | — |
| Nós de qualidade | setores/tags/estágio/UF/mecanismo/faixas TRL | — |
| `tem_setor`, `tem_tecnologia`, `busca_estagio`, `tem_uf`, `usa_mecanismo`, `tem_trl_faixa` | colunas do gold | `phase1_deterministic` |
| `operado_por`, `subordinado_a`, `credenciada_por` | `entity_relationships` | `phase1_structural` |
| `similar_a` | cosseno dos embeddings existentes (threshold ≥ 0.75, top-10) | `phase1_similarity` |
| `potencial_parceria` | Jaccard de tecnologia compartilhada edital↔ICT | `phase1_tech_bridge` |

`similar_a` e `potencial_parceria` são **derivadas** (`properties.derived=true`)
— nunca apresentadas como fato documental. O hub `setor:multissetorial` existe
na topologia com `weight=0.1` (`properties.hub=true`) e não expande vizinhança
(a travessia passa `min_weight >= 0.5`).

## 3. Modelo de gerações e troca atômica

`kg_phase1.generations` é o ledger + ponteiro da corrente:

1. o build lê o gold, monta a projeção **em memória** (funções puras) e grava
   uma NOVA geração dentro de **uma única transação**;
2. o swap (`is_current = (id = nova)`) é a **última operação** da transação —
   no mesmo commit a nova geração vira `healthy` + `is_current=true`;
3. leitores resolvem a geração corrente via `is_current = true` → **nunca**
   observam uma geração incompleta (`building`/`failed` nunca é `is_current`);
4. falha → rollback → a última geração saudável permanece corrente; um registro
   `failed` (best-effort, transação separada) fica no ledger com **apenas**
   `categoria:tipo` (nunca a mensagem da exceção);
5. idempotente: mesma `source_hash` do gold na geração corrente → build pula.

O volume atual (~242 nós, ~2.4k arestas) torna a reconstrução completa trivial —
uma única transação, sem infraestrutura distribuída.

## 4. Determinismo total

A mesma entrada produz EXATAMENTE os mesmos nós, arestas (na mesma ordem),
comunidades (mesmos IDs) e `source_hash` — independente da ordem devolvida
pelo Postgres, da ordem das listas de entrada, da iteração de `set` e do
`PYTHONHASHSEED`. Regras:

- **leitura ordenada**: `_load_gold` faz `order by` em `entities` e
  `entity_relationships`;
- **ordem canônica total**: nós e nós de qualidade são devolvidos por `id`;
  arestas por JSON canônico (`sort_keys=True`, separadores `","`/`":"`), com o
  dedup (1ª ocorrência) aplicado APÓS essa ordenação;
- **iteração determinística**: `_similarity_edges` ordena por id do nó e usa o
  id como desempate do score; `_partnership_edges` itera `sorted(editais)` ×
  `sorted(icts)` (nunca `set` cru);
- **JSON canônico** em qualquer parte de hash/ordenação (`_json`);
- **comunidades**: IDs `com_<idx>` atribuídos DEPOIS de ordenar as comunidades
  pelo conjunto ORDENADO de membros; membros de cada comunidade também
  ordenados — a ordem incidental do Louvain nunca decide o ID;
- **leitores determinísticos**: `load_communities` ordena por
  `community_id, node_id`.

O fixture de equivalência da spike e os testes de embaralhamento/reversão e de
`PYTHONHASHSEED` (prova via subprocess, sem infraestrutura própria) garantem o
contrato.

## 5. Falha segura — categorias canônicas

Nem o ledger nem os logs contêm `str(exc)`. Persiste-se apenas:

```
<categoria>:<TipoSeguro>
```

| Categoria | Exemplos |
|---|---|
| `database_error` | `psycopg.*` (e `psycopg_pool.*`) |
| `dependency_error` | `ImportError`/`ModuleNotFoundError` |
| `contract_error` | `ValueError`/`KeyError`/`TypeError`/`DatabaseTargetError` |
| `unexpected_error` | demais exceções |

Sem regex de DSN, sem mensagem bruta, sem `logger.exception`, sem traceback —
mesmo no fallback do `_record_failure`. DSN, URL, SQL, trecho documental ou
segredo presentes na mensagem da exceção nunca chegam à persistência ou ao log
(verificado por testes adversariais).

## 6. Uso

```bash
# Pré-requisito: migration 048 aplicada (supabase migration up), ambiente local.
pip install -e ".[dev]"       # testes com networkx (CI) — extra funcional é [graph]
pip install -e ".[graph]"     # runtime opcional: networkx p/ comunidades

DATABASE_URL=postgresql://... python -m radar.core.kg.phase1.ingest          # build/sync
DATABASE_URL=postgresql://... python -m radar.core.kg.phase1.ingest --no-skip # força rebuild
```

O CLI exige o guard de ambiente padrão (`assert_database_target`) — produção
exige `ALLOW_PRODUCTION_MUTATION=1` + `CONFIRM_PROJECT_REF` (mesmo padrão do
gold). O ledger e os logs são sanitizados por categoria (sem conteúdo de
documentos, URLs de DSN ou payloads).

## 7. API interna (sem endpoint HTTP nesta etapa)

| Operação | Função |
|---|---|
| construir/sincronizar geração | `ingest.build(skip_unchanged=…, run_communities=…)` |
| geração saudável corrente | `store.current_generation()` |
| consultar nós / qualidade / arestas | `store.load_nodes()` / `load_quality_nodes()` / `load_edges()` / `query_edges(…)` / `get_node(id)` |
| percorrer caminhos limitados | `traverse.bfs_edges` / `find_paths` / `reachable_within` (funções puras sobre `store.load_edges()`) |
| comunidades / centralidade | `store.load_communities()` / `features.stored_node_stats()` (centralidade "quando disponível") |

## 8. Degradações deliberadas

- **networkx ausente** (sem `[graph]`/`[dev]`): o build roda sem comunidades
  (`counts.communities = 0`) e `node_stats`/`stored_node_stats` devolvem `{}` —
  features são enriquecimento, não contrato. A dependência é extra `[graph]`
  funcional + extra `[dev]` (CI roda o teste real); **não** está nas
  dependências principais — o worker já recebe `networkx` pelo lock atual
  (`requirements.worker.lock.txt`, via `alphashape`), sem regenerar locks.
- **Sem geração corrente**: leitores devolvem listas vazias / `None` (estado
  pré-primeiro-build), nunca um dado parcial.

## 9. Fora do escopo (KG-P1A)

- Fase 2 (extração LLM), `extraction_candidates`, promoção de predicados;
- integração com o Explorar (tools), Match/`match_v3`, RAG, memória;
- endpoint HTTP; cron/worker; flags de produção; deploy.
