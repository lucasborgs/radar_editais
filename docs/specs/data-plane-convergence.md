# Spec — Convergência do data plane de editais

**Status:** vigente · **Data:** 2026-07-15
**Documento-pai:** [`system-coherence.md`](system-coherence.md)
**Perfis afetados:** usuário técnico e operador
**Impacto:** médio; documentação autoritativa, helpers e tooling do data plane,
sem alteração de comportamento de produto ou dados persistidos

## 1. Problema comprovado

O runtime de catálogo e match já convergiu para o gold relacional, mas o
repositório ainda apresenta caminhos antigos e camadas atuais como se fossem
alternativas equivalentes:

1. `docs/domain/schema.md` declara corretamente que o gold v3 é relacional, mas ainda mantém
   schema, prompt e workflow de geração de wiki pages JSON; em §12 continua
   chamando wiki page de “Knowledge gold”, embora o produtor hyper-extract e
   `build_knowledge_graph` tenham sido removidos;
2. `src/radar/core/kg/edital_id.py` ainda expõe `wiki_page_path()` e
   `iter_wiki_pages()`. Seus únicos consumidores são testes e o script one-shot
   `scripts/migrate_existing_ids.py`; não existe diretório rastreado
   `data/knowledge_graph/wiki/` nem consumidor de runtime;
3. `scripts/migrate_existing_ids.py` prepara a migration 012 e o cache de
   `etl_process`, enquanto a main já contém a migration 036 e não contém
   `etl_process`;
4. `docs/specs/lazy-chunking.md` se declara vigente e descreve ingestão
   exclusivamente sob demanda, mas `src/radar/core/tasks.py` possui o cron vivo
   `warm_edital_chunks` às 05:00 UTC. O runtime atual é híbrido: aquecimento do
   catálogo mais ensure/prefetch sob demanda;
5. comentários e docstrings de `run_daily_etl` ainda dizem que o cron enfileira
   chunking, apesar de o próprio corpo declarar que essa etapa não ocorre ali;
6. `src/radar/core/db.get_supabase()` está marcado deprecated e não possui consumidor;
7. `scripts/generate_golden.py`, tooling ativo da suíte RAG, instancia
   `OpenAI()` diretamente apesar do contrato único de `radar.core.llm.llm_client`; e
8. paths distintos que têm responsabilidades legítimas — bronze,
   `edital_source_docs`, silver, gold, `match_chunks` e `edital_chunks` — não
   estão descritos em uma única fronteira, favorecendo a leitura incorreta de
   que são pipelines concorrentes.

O problema não é haver várias representações. É não ficar inequívoco quando
elas são camadas do mesmo fluxo, índices especializados ou resíduos sem runtime.

## 2. Resultado pretendido

Uma pessoa técnica deve conseguir seguir qualquer edital por um caminho único:

```text
fonte
  → bronze imutável
  → Documento Canônico (adapter; Postgres durável com fallback local)
  → silver estruturado
  → gold relacional (entities + relationships + match_chunks)
  → catálogo / Explore / Radar

Documento Canônico
  → chunk_edital
  → edital_chunks
  → RAG de escrita
```

Descobertas aprovadas entram nesse mesmo fluxo. Nenhuma wiki page JSON funciona
como catálogo, fallback de produto ou etapa intermediária do v3.

## 3. Fronteiras canônicas

| Responsabilidade | Caminho canônico | Papel |
|---|---|---|
| captura por fonte | `src/radar/pipeline/extractors/` → `data/bronze/` | evidência bruta imutável |
| normalização documental | `src/radar/pipeline/adapters/` | `CanonicalDoc` agnóstico por fonte |
| durabilidade do documento | `edital_source_docs` via `src/radar/core/kg/source_docs.py` | autoridade durável; disco é fallback/cache local |
| estrutura intermediária | `data/silver/structured_docs/*.jsonl` | derivado reproduzível para ingestão |
| catálogo e relações | `entities` + `entity_relationships` | leitura de catálogo e Explore |
| ranking de match | `match_chunks` | índice contextual do match v3 |
| RAG de escrita | `edital_chunks` | índice documental fino, aquecido e garantido sob demanda |
| descoberta antes do catálogo | `discovered_opportunities` | staging com gate humano |
| jobs | `src/radar/core/tasks.py` + Procrastinate | única orquestração periódica/de fila |

`match_chunks` e `edital_chunks` não serão unificados: possuem produtores,
granularidade, custo e consumidores diferentes. `edital_source_docs` e bronze
também não são duplicatas: um é contrato durável normalizado; o outro preserva
a evidência específica da fonte.

## 4. Seams e camadas preservados

### 4.1 `kg_store`

`src/radar/core/kg/kg_store.py` permanece nesta spec. Seus consumidores vivos são:

- ledger operacional e deduplicação complementar da Descoberta; e
- corpus offline de evidências do vocab lint.

Isso não o transforma no backend do catálogo. A migração desses consumidores
continua condicionada ao item verificável em `docs/BACKLOG.md`.

### 4.2 Perfil anônimo e autenticado

O espelho em `localStorage` e `workspaces.profile` é uma ponte deliberada:
anônimos precisam de estado local; usuários autenticados têm Postgres como
autoridade e fazem merge explícito do perfil local. Não entra nesta spec.

### 4.3 Clientes de banco

`get_supabase_service()` e o cliente por JWT não concorrem: representam a
fronteira service-role versus RLS. O alias sem consumidor `get_supabase()` pode
ser removido, mas as duas factories autoritativas permanecem.

### 4.4 Clientes LLM

`llm_client` é a factory do SDK OpenAI-compatible. O runtime LangGraph constrói
ChatModels LangChain porque precisa de `bind_tools` e callbacks nativos; essa
diferença de interface é legítima. Tooling que usa o SDK diretamente deve
convergir para `llm_client`.

## 5. Escopo de execução

### Etapa 1 — Reconciliar autoridade e história

1. tornar a arquitetura de camadas atual inequívoca em `docs/domain/schema.md`, preservando
   somente os blocos YAML ainda lidos por consumidores comprovados;
2. mover instruções de geração de wiki pages e workflow hyper-extract para
   histórico quando tiverem valor explicativo;
3. substituir a spec `lazy-chunking.md` como autoridade atual por uma referência
   fiel ao modelo híbrido warm + on-demand; e
4. reconciliar `README.md`, `AGENTS.md`, `docs/architecture.md` e specs que
   descrevem wiki JSON como data plane vivo.

Mudanças de regras de domínio continuam sendo feitas em `docs/domain/schema.md`; esta etapa
remove apenas descrições e schemas de um produtor comprovadamente ausente.

### Etapa 2 — Remover superfície sem consumidor

Somente após nova busca de referências:

1. remover `wiki_page_path()` e `iter_wiki_pages()`;
2. remover seus testes exclusivos e `KG_WIKI_DIR` se nenhum consumidor restar;
3. remover o script one-shot `migrate_existing_ids.py`; e
4. remover `radar.core.infra.db.get_supabase()`.

`make_id`, `parse_id`, `source_of`, `native_id_of`, `id_to_slug` e
`slug_to_id` permanecem: são contratos vivos de identidade cross-source e do
vault Obsidian.

### Etapa 3 — Alinhar o runtime descrito

1. corrigir comentários/docstrings de `run_daily_etl` que ainda prometem
   chunking no cron das 03:00;
2. documentar `warm_edital_chunks` como aquecimento idempotente das 05:00 e o
   ensure/prefetch como rede de segurança;
3. manter os produtores atuais e seus horários; e
4. não renomear tabelas, filas ou tasks sem necessidade funcional.

### Etapa 4 — Convergir tooling comprovado

1. migrar `scripts/generate_golden.py` para `radar.core.llm.llm_client.make_client`;
2. verificar scripts rastreados que alegam consumir wiki pages ou pipelines
   removidos;
3. preservar benchmarks independentes quando sua independência for parte do
   experimento e estiver declarada; e
4. não tocar nos artefatos locais de avaliação protegidos e não rastreados.

## 6. Fora de escopo

- remover ou migrar `kg_store`, `kg_artifacts`, índices JSON ou o ledger;
- escolher entre aquecimento eager e chunking lazy; o comportamento atual será
  apenas descrito fielmente;
- unificar `match_chunks` e `edital_chunks`;
- alterar scraping, promoção, ranking, elegibilidade, retrieval ou agentes;
- mudar schemas, migrations, dados remotos, IDs ou APIs;
- remover bronze/silver, documentos canônicos ou fallbacks de resiliência;
- redesenhar persistência do perfil; e
- converter esta limpeza em recomendação arquitetural ou roadmap.

## 7. Critérios objetivos de remoção

Um helper, arquivo ou constante só pode ser removido quando:

1. `rg` não encontrar consumidor fora de testes exclusivos, documentação
   histórica ou do próprio artefato candidato;
2. não for entrypoint documentado em `AGENTS.md`, deploy, CI ou package manifest;
3. não participar de migration ainda necessária para instalar o banco atual;
4. não for lido dinamicamente por registry, glob, import tardio ou loader YAML;
5. o contrato vivo equivalente estiver identificado; e
6. testes proporcionais passarem após a remoção.

Em dúvida, o artefato permanece e a razão é registrada.

## 8. Invariantes

1. catálogo, Explore e Radar continuam lendo o gold relacional;
2. escrita continua lendo `edital_chunks` e nunca `match_chunks` como substituto;
3. Descoberta continua em staging até promoção humana;
4. promoção e ETL diário continuam convergindo em silver → gold;
5. Documento Canônico durável continua com fallback local;
6. `kg_store` continua atendendo seus consumidores atuais;
7. service-role não entra em rotas de usuário fora das exceções administrativas
   já protegidas; e
8. nenhuma mudança desta spec altera resposta, ranking ou jornada de produto.

## 9. Reversibilidade

- documentação histórica preserva contexto dos fluxos removidos;
- remoções são pequenos commits separados e recuperáveis por revert;
- nenhuma migration ou mutação de dados faz parte da execução;
- mudanças de factory mantêm modelo, prompt, timeout e formato de saída; e
- qualquer consumidor dinâmico descoberto interrompe a remoção correspondente.

## 10. Validação

- `git diff --check`;
- `ruff check .`, excluindo apenas artefatos locais protegidos já existentes;
- testes direcionados de `edital_id`, schema/WIKI, tasks, source docs, kg_store,
  RAG e geração de golden afetados;
- suíte hermética completa quando a amplitude das remoções justificar;
- `rg` final sem referências correntes a wiki pages JSON como catálogo vivo,
  `build_knowledge_graph`, `etl_process` ou helpers removidos;
- confirmação de que `match_chunks` possui apenas o produtor gold e
  `edital_chunks` apenas o produtor `chunk_edital`;
- confirmação de que os arquivos locais protegidos seguem não rastreados; e
- nenhuma validação externa ou mutação remota é necessária.

## 11. Critérios de conclusão

1. a documentação corrente apresenta um único data plane de editais;
2. camadas intencionais estão diferenciadas de caminhos concorrentes;
3. wiki pages JSON não aparecem como componente do runtime v3;
4. o modelo híbrido de chunks de escrita está descrito sem contradição;
5. helpers e scripts one-shot sem consumidores comprovados foram removidos ou
   preservados com justificativa;
6. tooling ativo usa as factories canônicas aplicáveis;
7. `kg_store` permanece funcional e corretamente delimitado; e
8. comportamento de produto, schemas e dados permanecem inalterados.
