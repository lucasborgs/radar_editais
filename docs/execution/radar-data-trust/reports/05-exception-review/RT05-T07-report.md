# RT05-T07 — Enforcement temporal e read model em lote

**Data:** 2026-07-29
**Base:** `138ab38cf`
**Branch:** `codex/radar-data-trust-05-t07`
**Worktree:** `/private/tmp/radar-editais-rt05-t07`
**Commit funcional:** `db9bfa83e`
Auditoria Codex: pendente

## Resultado

Foi introduzido um único read model temporal em lote. Ele reutiliza
`evaluate_temporal()` (T01), a projeção de revisão T04 e a fila/revisões
persistidas. Seu payload público seguro contém somente:

- `temporal_mode`;
- `validity_state`;
- `temporal_value`;
- `decision_source`; e
- `last_verified_at`.

Não expõe ID de exceção, revisão, autor, justificativa, evidência ou payload
administrativo. Não há migration, tabela, coluna, cache, backfill nem cópia de
fatos/revisões em `entities`.

## Inventário prévio dos caminhos temporais

Antes desta task, foram encontrados os seguintes comportamentos produtivos:

| Superfície | Caminho anterior | Risco corrigido nesta task |
|---|---|---|
| Match / Stage 0 | `match_v3.stage0_alive()` fazia `deadline=null` passar como fluxo contínuo quando status era nulo/aberto/ativo. | Agora recebe exclusivamente o read model e só aceita `validity_state=active`. |
| Payload do Radar | `find_matching_opportunities()` convertia qualquer item com prazo em `status="aberta"`. | Status, prazo e campos temporais vêm da mesma projeção. |
| Ecossistema / catálogo | `_status_from_row()` recomputava aberto/encerrado pelo prazo e, sem prazo, aceitava o status bruto aberto. | Cards, listas e fichas usam o lote canônico; legado sem avaliação fica `needs_review`. |
| Explorar | Tools chamavam `entity_catalog.list_editais()` e `get_edital()`. | Herdam o card canônico sem lógica temporal própria. |
| Aplicações | `_build_pipeline_items()` chamava `get_edital()` por aplicação. | Agora carrega os cards de editais/programas uma vez, incluindo um único lote temporal. |
| Sessões de Escrita | `_attach_target_titles()` chamava `get_edital()`/`get_investidor()` por sessão. | Agora consulta apenas `native_id,name` em um lote; títulos não carregam temporalidade. |
| Escrita / critic | `temporal_context()` descrevia todo prazo ausente como fluxo contínuo. | Durante a transição, `needs_review` e `closed` sem prazo retornam bloco vazio; somente `continuous/active` mantém a mensagem preexistente. |
| Routers | `/editais`, `/oportunidades` e `/opportunities` apenas repassavam `entity_catalog`; `/radar/matches` serializava `OpportunityMatch`. | Os dois payloads agora carregam os mesmos cinco campos temporais seguros. |

Checklist, Planning e Critic chamam um único edital por operação, portanto não
formam um loop novo. Explore usa as tools do catálogo; Escrita usa o bloco de
`radar.core.kg.temporal`; não foi criado prompt ou serializador concorrente.

## Implementação

1. `temporal_read_model.py` recebe vários sujeitos, faz uma carga em lote de
   exceções e uma de revisões, e aplica a projeção T04 já carregada. Falha de
   leitura é logada somente por categoria/quantidade e torna todo o lote
   `unknown/needs_review`.
2. O repositório T02 ganhou apenas as duas leituras necessárias, com seleção
   estrita de campos. A consulta de exceções é por `subject_id IN (...)`; a de
   revisões é por `exception_id IN (...)`. Não há consulta dentro do loop dos
   consumidores.
3. Match resolve o lote do snapshot uma vez antes do Stage 0. `closed` e
   `needs_review` não alcançam ranking, elegibilidade, embeddings ou rerank.
4. Catálogo resolve cada coleção de editais/programas uma vez, e preserva
   investidores no card curado anterior: status `ativa/inativa`, sem campos
   temporais e sem leitura da fila. `get_opportunity()` não chama o read model
   para `investidor:`; ICTs e demais atores permanecem fora dessa projeção.
5. Aplicações injeta o mapa de cards em lote. A lista de sessões de Escrita usa
   `get_opportunity_titles()` e, por precisar apenas de título, não consulta
   exceções/revisões. Os testes cobrem ambos os caminhos e a ausência de carga
   temporal para investidores.
6. O prazo igual a `as_of` segue `active`, conforme `evaluate_temporal()` e o
   relógio em `America/Sao_Paulo`. `ABERTA` sem prazo, incluindo Finep/Eureka,
   fica `unknown/needs_review` até revisão documental válida.
7. Match passa a obter o default do dia em `America/Sao_Paulo`; `as_of`
   explícito continua prioritário. O teste injeta uma data em que UTC já mudou
   de dia e São Paulo ainda não.
8. A comunicação final continua em T08: `needs_review` e `closed` sem prazo
   retornam bloco vazio. Os textos preexistentes de prazo fixo e de fluxo
   contínuo só são preservados quando o read model permite essa afirmação.

## Validação

Executados no worktree, sem rede, LLM ou banco externo:

- `347 passed, 5 skipped` — read model, consumidores em lote, Match, contexto
  temporal, projeção T04, contratos T01–T05, detector, repositório, API,
  Aplicações, catálogo e proveniência;
- `ruff check` no escopo alterado — aprovado;
- `git diff --check` — aprovado.

O teste novo do read model comprova que três sujeitos usam exatamente uma carga
de exceções e uma de revisões, que uma revisão contínua válida libera apenas o
respectivo sujeito, que Finep/Eureka sem revisão fica `needs_review` e que falha
de carga não concede atividade. Os testes de consumidores comprovam que N
Aplicações usam uma chamada de cartões em lote e que N sessões de Escrita usam
uma única leitura de títulos, sem `get_edital()` unitário ou temporalidade.

## Fora de escopo preservado

T08 não foi iniciada. Não houve alteração de frontend, layout, comunicação UX,
ranking, relevância, elegibilidade, rerank, extração, LLM, prompt independente,
worker, migration, backfill ou push/merge. A remoção de duas linhas inválidas
em `writing.py`, já presentes na base e que impediam importar a rota testada,
foi o reparo mínimo necessário para validar a listagem em lote; não altera
comportamento de produto além de restaurar a sintaxe do fallback existente.
