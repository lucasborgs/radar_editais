# Backlog técnico atual

> Reconciliado em 2026-07-14 contra a implementação gold v3 da `main`.
> Este arquivo não é roadmap de produto: registra somente trabalho técnico
> adiado que ainda tem evidência no repositório ou validação externa necessária.
> Ideias, experimentos e decisões anteriores estão preservados no
> [snapshot histórico](historical/backlog-pre-gold-v3-2026-07-14.md).

## Critério de manutenção

Um item só permanece aqui quando contém:

1. evidência verificável no código, schema ou configuração atuais;
2. motivo explícito para não agir agora;
3. gatilho objetivo de retomada;
4. ponto de entrada que ainda existe.

Itens concluídos, refutados, substituídos pelo gold v3 ou puramente de produto
não ficam no backlog técnico. Estado de produção que o Git não comprova é
rotulado como **estado externo não verificado**, nunca como fato atual.

## Pendências verificadas

### Ativar o gate bloqueante de matching somente com casos reais revisados

- **Evidência atual:** `core/eval/matching.py` mantém a suíte como `candidate`.
  O contrato v2 mede MRR, recall@10, hard negatives, falsos positivos
  confirmados e resultados não julgados no top-8. A última rodada diagnóstica
  completa registrou recall `0,5237` e média de `4,5` resultados não julgados;
  os perfis atuais do golden são sintéticos.
- **Motivo do adiamento:** ainda não há casos reais representativos nem
  julgamentos humanos suficientes para transformar essas métricas em decisão
  obrigatória de merge/release.
- **Gatilho:** amostra representativa de uso real, com o top-8 classificado por
  perfil como relevante, neutro defensável ou irrelevante confirmado.
- **Ponto de entrada:** `core/eval/matching.py`,
  `eval_data/golden/matching.json` e
  `docs/specs/evaluation-operations.md`.
- **Restrição:** a suíte permanece diagnóstica; esta pendência não bloqueia,
  desativa ou altera a feature de match no produto.

### Manter a escrita automática de memória congelada até haver evidência real

- **Evidência atual:** `AUTO_MEMORY_WRITE=0` é o default em
  `core/reflection_service.py`, `core/tasks.py` e `.env.example`; leitura de
  memória permanece ativa. As tabelas `playbook_overlays` e
  `meta_reflection_runs` existem, mas o job `run_meta_reflection` continua apenas
  documentado como scaffold na migration 024.
- **Motivo do adiamento:** promover aprendizado compartilhado sem outcomes reais
  e curadoria aumenta o raio de impacto entre workspaces.
- **Gatilho:** outcomes reais suficientes, provenientes de mais de um workspace,
  e definição de curadoria humana antes de escrita compartilhada.
- **Ponto de entrada:** `core/reflection_service.py`, `core/tasks.py`,
  `core/skills.py` e `supabase/migrations/024_playbook_overlays.sql`.
- **Restrição:** não religar a flag nem criar o job apenas para completar o
  scaffold.

### Reduzir o seam legado de `kg_store` somente após migrar os consumidores vivos

- **Evidência atual:** catálogo e match usam as tabelas gold, mas
  `core/opportunity_discovery.py` ainda usa `kg_store` para o
  `discovery_ledger` e consulta `load_index()` na deduplicação; `core/vocab_lint.py`
  também lê `index.json`/`index_historico.json` por esse seam. Não há outros
  consumidores de produção/tooling no repositório; `load_icts()` aparece apenas
  nos testes. `tests/test_kg_store.py` cobre os contratos ainda expostos.
- **Motivo do adiamento:** remover `kg_store`, `kg_artifacts` ou os helpers de
  índice agora alteraria deduplicação/observabilidade da Descoberta e quebraria
  tooling offline. Não são arquivos mortos.
- **Gatilho:** substituir os dois consumidores de índice por consultas gold e
  dar ao ledger um store explícito com teste de compatibilidade/migração.
- **Ponto de entrada:** `core/kg/kg_store.py`, `core/opportunity_discovery.py`,
  `core/vocab_lint.py`, migrations 016 e 033.
- **Restrição:** preservar leitura do ledger existente durante qualquer migração.

### Executar gates que dependem de serviços reais quando houver ambiente autorizado

- **Evidência atual:** parte das suítes de tenant isolation, RAG, writing e
  telemetria exige Postgres/Supabase, chaves LLM ou Langfuse reais; os prereqs
  estão declarados no harness e em `AGENTS.md`.
- **Motivo do adiamento:** a suíte hermética valida código local, mas não prova
  policy remota, emissão de traces nem comportamento de providers externos.
- **Gatilho:** preparação de release/deploy ou alteração na integração
  correspondente.
- **Validação:** rodar apenas a suíte afetada com ambiente explicitamente
  autorizado, registrar commit/configuração e não confundir falha de rede com
  regressão de código.
- **Tipo:** gate operacional; não autoriza mudança de arquitetura.

## Decisões atuais que não são backlog

- `RERANK_BACKEND=off` em produção e o extra opcional `.[rerank]` são uma escolha
  operacional documentada, não uma pendência automática.
- `domain.vocabulary.canonicalize_themes` é um normalizador parcial usado pelo
  vocab lint; novos sinônimos só entram com evidência do corpus e atualização do
  vocabulário autoritativo em `WIKI.md`.
- `requires_ict_partner` permanece no contrato avaliado de extração e não é gate
  de elegibilidade nem sinal consumido pelo match atual.

## Resultado da reconciliação de 2026-07-14

Os 55 itens do documento anterior foram preservados no snapshot histórico e
retirados da lista viva pelos seguintes motivos predominantes:

- arquitetura substituída: `build_knowledge_graph`, `etl_process`, `index.json`,
  wiki pages JSON, `HybridMatch` e match por hipergrado;
- entrega já incorporada: hardening pré-beta, BM25/HyDE, fluxo de staging e
  promoção da Descoberta, match gold v3;
- hipótese refutada ou experimento encerrado: parsing/chunking alternativo e
  Stage 2a generativo do matcher legado;
- roadmap/feature: budget builder, novos botões, novas fontes, GraphRAG e
  extensões de produto;
- estado remoto datado: migrations e branches cuja situação atual não pode ser
  inferida de um texto histórico.

Nenhum desses itens foi declarado resolvido sem evidência; o texto original foi
arquivado justamente para preservar contexto e permitir auditoria.
