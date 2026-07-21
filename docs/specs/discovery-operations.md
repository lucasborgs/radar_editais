# Spec — Operação da descoberta

**Status:** vigente para as rotas de promoção e retry · **Data:** 2026-07-14

**Documento-pai:** [system-coherence.md](system-coherence.md).

## 1. Resultado de produto

Dar ao operador uma visão confiável de cada oportunidade descoberta depois da
aprovação humana: qual rota de ingestão foi usada, o que já chegou ao catálogo
e ao RAG, o que falhou e o que pode ser reprocessado sem publicar conteúdo não
aprovado.

```text
Torneira de descoberta
  → staging global (pending)
  → revisão humana: promover ou rejeitar
  → execução rastreada por etapa
      ├─ fonte / bronze → silver → gold → Radar
      └─ documento → chunks → embeddings → RAG
  → pronto, falha parcial ou retry explícito do operador
```

O resultado visível ao cliente continua sendo somente conteúdo já aprovado e
publicado nas superfícies próprias: catálogo/Radar e RAG são resultados
independentes da mesma promoção, não sinônimos.

## 2. Situação atual e problema

Hoje `discovered_opportunities` registra apenas a decisão editorial
`pending | promoted | rejected`. A promoção tem duas rotas:

- **URL/página:** cria ou reativa `web_sources`; o scraper web a coleta em
  ciclo posterior e o pipeline normal produz bronze, silver, gold e corpus.
- **PDF direto:** baixa o arquivo, extrai texto para bronze `web`, e enfileira
  `ingest_promoted_edital` (silver → gold/catálogo) e `chunk_edital`
  (chunking contextual e embeddings/RAG).

Isso preserva o caminho nativo para conteúdo promovido, mas a decisão
`promoted` não diz se o conteúdo já foi obtido, se entrou no Radar, se os
chunks ficaram disponíveis nem onde uma falha ocorreu. Também não há uma forma
operacional de repetir somente a etapa necessária.

### Relação com a avaliação Crawl4AI

O bake-off local de 2026-07-13 atuou **antes** desta spec e reprovou a hipótese
de substituir os adapters por um extrator universal. A decisão e as métricas
relevantes estão registradas em
[crawl4ai-discovery-integration.md](crawl4ai-discovery-integration.md). Esta
entrega começa somente após o registro estar em `discovered_opportunities` e um
operador o aprovar.

As duas linhas devem compartilhar um contrato de evidência, não uma
implementação acoplada:

- o pipeline atual continua autoritativo; o coletor Crawl4AI opcional entrega
  URL canônica, texto recuperável, links de PDF/documentos e metadados de coleta;
- staging preserva referências a esses artefatos para revisão, mas nenhum deles
  é publicado antes da aprovação; e
- a promoção escolhe explicitamente quais documento(s) aprovados alimentam
  bronze, silver, gold/Radar e chunks/RAG, registrando-os no run.

**Decisão de implementação:** preservar os scrapers dedicados e manter
Crawl4AI como enriquecimento opcional do worker, atrás de flag. A rota
`direct_pdf`, o modelo de execução, a auditoria e a separação Radar/RAG não
dependem do coletor. Crawl4AI não é dependência obrigatória do backend.

## 3. Decisões propostas

| # | Decisão |
|---|---|
| D1 | `discovered_opportunities.status` continua sendo o estado editorial: `pending`, `promoted` ou `rejected`. O progresso técnico vive em uma execução de promoção separada; “promovido” nunca significa implicitamente “pronto”. |
| D2 | Uma promoção cria uma `promotion_run` auditável, com a rota (`web_source` ou `direct_pdf`), identificadores de fonte/documento, etapas, tentativas e timestamps. Os eventos de etapa são append-only. |
| D3 | As superfícies têm prontidão independente: `radar_ready` significa que o edital foi estruturado e ingerido no gold que alimenta catálogo/match; `rag_ready` significa que há chunks indexados e pesquisáveis. O estado geral só é `ready` quando ambas as superfícies exigidas pela rota estiverem prontas. |
| D4 | Uma URL/página promovida não pode declarar `bronze_ready`, `radar_ready` ou `rag_ready` antes da coleta real. Enquanto depende do scraper, aparece como `awaiting_fetch`, com a fonte e o próximo passo explícitos. |
| D5 | PDF direto segue os mesmos componentes nativos de conteúdo `web`: bronze → structurer/silver → `gold.ingest_all` para o Radar; e `chunk_edital` → contextual retrieval → embeddings para RAG. Não haverá pipeline paralelo de parsing ou embedding. |
| D6 | Falha em uma superfície não desfaz a outra. Por exemplo, gold concluído e chunking falho é `radar_ready` + `rag_failed`; o operador pode repetir somente RAG. |
| D7 | Retry é ação explícita de operador, idempotente e limitado a uma etapa falha ou pendente. Ele reaproveita os artefatos válidos e não recria `web_sources`, bronze, registros gold ou chunks sem necessidade. |
| D8 | A interface e APIs desta entrega são administrativas. Staging, erros internos, links de job e histórico não são expostos no Radar, em Explorar nem a usuários finais. |

## 4. Modelo de estados

### 4.1 Decisão editorial

| Estado | Significado |
|---|---|
| `pending` | achado da torneira aguardando decisão humana; não entra em fonte curada, gold ou RAG. |
| `rejected` | descartado pelo operador, com motivo opcional; não pode ser promovido por acidente. |
| `promoted` | aprovação editorial concluída e uma execução técnica foi criada; consultar a execução para saber sua prontidão. |

### 4.2 Etapas da execução

Cada `promotion_run` registra uma etapa com `pending`, `running`, `ready`,
`failed` ou `not_applicable`, mais início/fim, contador de tentativas, IDs de
jobs/artefatos quando disponíveis e erro seguro para o operador.

| Etapa | Rota `web_source` | Rota `direct_pdf` | Pronto quando |
|---|---|---|---|
| `source_ready` | cria/associa `web_sources` | valida a URL PDF escolhida | a fonte aprovada está identificada e auditável |
| `bronze_ready` | aguarda scraper e seu conteúdo bruto | baixa, extrai e persiste conteúdo bruto | há documento fonte recuperável para o `edital_id` |
| `silver_ready` | após estruturação normal | após estruturação normal | existe documento estruturado válido para ingestão |
| `radar_ready` | após `gold.ingest_all` incremental | após `ingest_promoted_edital` | o edital está no gold/catálogo consumido pelo match v3 |
| `rag_ready` | após `chunk_edital` | após `chunk_edital` | chunks contextualizados e embeddings do documento estão pesquisáveis |

`awaiting_fetch` é um estado agregado de execução, não uma falsa etapa pronta:
é usado na rota de página quando `source_ready` está pronto e a primeira coleta
ainda não materializou bronze. `partial_failure` resume uma ou mais etapas
falhas com outra superfície pronta; `failed` significa que nenhuma superfície
requerida ficou pronta; `ready` exige `radar_ready` e `rag_ready`.

## 5. Dados, auditoria e contratos

### Dados persistidos

Adicionar migrations para duas tabelas globais, service-role-only, ligadas a
`discovered_opportunities`:

- `discovery_promotion_runs`: uma execução por aprovação/retry, com
  `discovered_opportunity_id`, rota, estado agregado, `edital_id`,
  `web_source_id`, URLs normalizadas, timestamps, versão/contagem de tentativa
  e resumo de etapas; e
- `discovery_promotion_events`: histórico append-only de transições, retry,
  job enfileirado/concluído, artefato associado e erro sanitizado, com ator
  (`operator` ou `system`) e timestamp.

Erro persistido nunca inclui segredos, HTML/PDF bruto, headers de autenticação
ou stack trace integral. O conteúdo bruto continua onde já pertence (bronze e
documentos-fonte), sem duplicação no staging ou nos eventos.

### API administrativa

Manter `AdminUserId` em todos os endpoints. A forma exata de payload será
tipada durante a implementação, mas o contrato inclui:

- `POST /discovered-opportunities/{id}/promote`: mantém a aprovação humana,
  retorna `promotion_run` inicial e seus próximos passos; a resposta não
  promete ingestão concluída apenas porque jobs foram enfileirados.
- `POST /discovered-opportunities/{id}/promotion/retry`: recebe a etapa
  elegível (`fetch`, `silver`, `radar` ou `rag`), cria um evento de retry e
  enfileira apenas o trabalho necessário. Não aceita retry para `pending` ou
  `rejected`.
- `GET /discovered-opportunities?include_reviewed=true`: expõe na própria fila
  administrativa o resumo do último run, sem carregar eventos completos.

O endpoint de rejeição continua exclusivamente editorial. Uma tentativa de
promover/rejeitar de novo continua retornando conflito, evitando duas rotas de
publicação concorrentes.

## 6. Integração com jobs e publicação

1. A aprovação grava a decisão editorial e o run de forma atômica antes de
   enfileirar trabalho.
2. Cada worker atualiza sua própria etapa ao iniciar, concluir ou falhar. O
   worker não infere sucesso só por ter aceitado o job na fila.
3. Para PDF, o processamento inicial deve registrar bronze e o `edital_id`
   antes de deferir os dois ramos. `ingest_promoted_edital` marca silver e
   Radar; `chunk_edital` marca RAG após confirmar chunks persistidos.
4. Para página, o scraper normal deve reconhecer uma `web_source` promovida e
   associar a coleta ao run. Só então dispara/atualiza as etapas seguintes;
   não há atalho que indexe o resumo de descoberta como se fosse o edital.
5. A publicação no Radar é validada contra a presença no gold/catálogo, não
   apenas contra conclusão do structurer. A publicação no RAG é validada contra
   índice de chunks, não apenas contra arquivo salvo.
6. Um retry não executa todo o ETL diário e não muda ranking, ontologia,
   chunker, modelo de embedding ou regras de elegibilidade.

## 7. UX administrativa

A fila de descoberta ganha, para itens revisados, um resumo legível:

- rota de promoção e quando foi aprovada;
- estado agregado (`aguardando coleta`, `processando`, `pronto`, `falha
  parcial` ou `falhou`);
- duas superfícies explícitas: “disponível no Radar” e “disponível no RAG”;
- a etapa bloqueada, mensagem segura de erro e última atualização; e
- ação de retry apenas quando permitida, com confirmação que informa a etapa
  reexecutada.

O detalhe apresenta linha do tempo de eventos e links internos/identificadores
de fonte e edital. Não apresenta ao operador uma cópia do documento nem muda o
fluxo de revisão existente. O Radar público não recebe indicador de staging ou
de oportunidade em processamento.

## 8. Fora de escopo

- Nova fonte de descoberta, OCR de PDF escaneado ou suporte a anexos de e-mail.
- Alterar o gate humano, a política global/admin, TTL de fila ou deduplicação.
- Reescrever scraper, structurer, `match_v3`, ontologia gold, chunker ou
  modelo de embeddings.
- Expor a operação de descoberta ao cliente final, notificações automáticas ou
  SLA de processamento.
- Corrigir retroativamente todos os promovidos históricos sem uma operação de
  backfill separada e aprovada.

## 9. Critérios de aceite

1. Um item `pending` ou `rejected` não possui run apto a publicar nem aparece
   no catálogo/Radar ou RAG por causa da descoberta.
2. Ao promover uma página, a UI informa `awaiting_fetch`; ela não afirma que
   a oportunidade chegou ao Radar/RAG até que os artefatos existam.
3. Ao promover um PDF textual válido, as duas trilhas nativas são registradas;
   o resultado mostra separadamente a disponibilidade em Radar e RAG.
4. Falha de `chunk_edital` após gold pronto mantém `radar_ready`, registra
   `rag_failed` e permite retry somente da etapa RAG.
5. Falha no gold não cria prontidão falsa de Radar e não invalida chunks RAG
   que já estejam corretos.
6. Repetir retry e callbacks de job é idempotente: não duplica fonte, bronze,
   entidade gold, eventos de conclusão nem chunks.
7. Apenas operador autorizado pode ler detalhe, promover, rejeitar ou retry;
   payloads não expõem conteúdo bruto, segredo ou stack trace.
8. Testes unitários dos estados e integração dos endpoints/jobs, `pytest`,
   migration aplicada em Supabase local e QA manual das rotas página/PDF,
   falha parcial e retry passam.

## 10. Plano de implementação após aprovação

1. Confirmar o ponto de associação do scraper web e dos jobs ao run; criar a
   migration de runs/eventos e helpers transacionais/idempotentes.
2. Instrumentar promoção PDF e URL, coletor web, `ingest_promoted_edital` e
   `chunk_edital` para transições verificadas por artefato.
3. Expor resumo, detalhe e retry administrativo com autorização e erros
   sanitizados.
4. Construir o painel administrativo de estado/histórico e seus estados
   carregando/falha.
5. Cobrir a máquina de estados e cenários de falha/retry; executar testes e
   QA manual antes de atualizar esta spec para concluída.
