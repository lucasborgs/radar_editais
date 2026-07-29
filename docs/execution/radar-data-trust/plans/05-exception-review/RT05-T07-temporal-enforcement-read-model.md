# RT05-T07 — Enforcement temporal e read model em lote

## Objetivo

Aplicar a projeção T04 aos read models e ao Stage 0.
`needs_review` e `closed` não entram no match ativo; `active` preserva o fluxo.
Produz payload canônico para T08, mas não muda componentes ou textos de UX.

## Dependências

RT05-T04. Pode pousar antes da UI administrativa, mas não depende dela.

## Arquivos prováveis

- `src/radar/core/services/match_v3.py`;
- `src/radar/core/kg/entity_catalog.py`, `temporal.py` e serviço temporal T04;
- routers/serializadores que entregam oportunidades a frontend, Explore e Escrita;
- testes de match, catálogo e temporal existentes ou novos focados.

## Passos

1. Trocar leituras locais `deadline=null → continuous/active` pela projeção T04.
   Reconciliar `_normalize_status`, `_status_from_row`, `stage0_alive` e contexto
   temporal com `deadline >= hoje`.
2. Carregar estados em lote por sujeitos, ou incorporá-los à query/read model.
   É proibido consultar fila/review por item em loops de match, catálogo ou rota.
3. Excluir `closed`/`needs_review` do match e manter `active`, sem alterar
   ranking, relevância, elegibilidade ou atores.
4. Acrescentar ao payload somente `temporal_mode`, `validity_state`, fonte e
   última verificação seguras. Não copiar facts, reviews ou exceções para `entities`.
5. Legados sem avaliação ficam desconhecidos conservadores; sem tabela, cache,
   coluna ou backfill especulativo.

## Invariantes

- Um read model canônico governa backend, prompts e UI; não criar segunda fonte.
- Consulta em lote, sem N+1, e sem duplicar fatos/reviews em `entities`.
- Prazo no dia atual permanece ativo até fim do dia em São Paulo.
- Finep/Eureka só deixa de ser ativo nesta task, não no shadow T03.

## Testes mínimos

- Finep/Eureka não passa Stage 0; prazo de hoje/contínuo confirmado passam;
  fechado não passa.
- Lista/match usam carga em lote e não invocam repositório por item.
- Legado é conservador e payload não contém revisão/nota interna.
- Testes match/catálogo/temporal, `ruff check` e `git diff --check`.

## Critérios de aceite

- Nenhuma leitura produtiva equipara prazo nulo a fluxo contínuo.
- Enforcement não produz N+1 nem persistência/cache novo.
- Payload canônico suficiente existe para T08.

## Proibições

Sem frontend, layout/texto, LLM, prompt independente, OCR/visão, rerank,
extração, backfill, migration ou tabela/cache especulativo.

## Pare se

O estado não puder ser lido em lote sem regressão material, se algum consumidor
depender de prazo nulo como contínuo sem evidência migrável, ou se for preciso
persistir cópia de review/fato em `entities`.
