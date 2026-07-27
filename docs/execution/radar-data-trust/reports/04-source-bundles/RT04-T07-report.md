# RT04-T07 — Métricas diagnósticas, reconciliação e fechamento

**Base:** `30c000c14` (`merge(data-trust): land RT04-T06B bundle lineage`)
**Branch:** `codex/radar-data-trust-04-t07`
**Worktree:** `/private/tmp/radar-editais-rt04-t07`
**Status:** concluída

## Implementação

Foi adicionado o read model puro
`src/radar/core/services/source_bundle_metrics.py`. Ele recebe bundles,
referências factuais e resultados de composição já existentes; não consulta DB,
filesystem, rede, LLM ou runtime produtivo.

Métricas derivadas:

- sujeitos com bundle e sujeitos com ao menos uma versão `complete`;
- versões por sujeito;
- documentos por papel;
- fatos críticos (`FactProvenance`) com ao menos uma evidência contendo
  `bundle_hash` + `content_hash` recuperáveis;
- campos `conflicting` e precedências explicitamente declaradas; e
- atores sem conteúdo oficial e/ou sem bundle `complete`.

Ausência de denominador retorna `null`: a métrica não fabrica cobertura
factual, conflito ou resolução. O denominador factual conta fatos, nunca
`EvidenceRef`: duas evidências ligadas do mesmo fato contam como 1/1.
`precedence_applied` é entrada explícita do resultado de composição; não é
deduzido de data, nome, ordem ou número de evidências.

## Baseline de fixture

Sobre Web composto, FAPESC com retificação, ICT `partial` e FAPESC `partial`
posterior: 3 sujeitos, 2 com versão corrente `complete`, 2 versões FAPESC e 1
ICT sem bundle completo. Não havia fatos críticos nem resultados de composição
fornecidos ao baseline; suas métricas ficaram `null`.

O cenário legado é a ausência de bundle: ele não é atribuído artificialmente a
uma versão e continua fora de qualquer denominador não observado.

## Reconciliação

- `docs/domain/schema.md` documenta histórico `source_bundles`, leitura
  corrente `complete` e a linhagem aditiva de `EvidenceRef`.
- `docs/architecture.md` mostra o histórico append-only antes da projeção
  `edital_source_docs` e registra a limitação de `match_chunks`.
- A spec 04, a spec-mãe, o plano e o índice de documentação foram atualizados
  para estado vigente/concluído.
- Busca estática confirmou uma única tabela nova (`source_bundles`, migration
  044), os produtores Web/FAPESC/atores já implementados e ausência de novo
  crawler, harness de avaliação, API de escrita ou chamada LLM introduzida por
  RT04.

## Limitações mantidas deliberadamente

- Sem backfill integral, migration adicional, dashboard, API, alerta, threshold
  ou gate.
- `match_chunks` permanece legado quanto à linhagem de bundle.
- Composição só trabalha com claims explícitos; não interpreta texto nem
  infere documento consolidado.
- Conflitos e lacunas de ator aguardam as frentes RT05 e RT06, respectivamente.

## Validação

- testes RT04 direcionados + métricas: **194 passed** (reexecutados após a
  correção do denominador factual);
- suíte completa anterior: **1799 passed, 77 skipped**;
- `python -m radar.core.eval run provenance` local, sem `--publish`:
  `aggregate_signals=1.0`, faithfulness `1.0`, locator exact/document-only/
  unresolved `1/3` cada;
- Ruff em Python alterado: aprovado; e
- `git diff --check`: aprovado.

A suíte completa não foi repetida após esta correção localizada no read model
e em seu teste. As únicas mensagens daquele gate são cinco warnings
pré-existentes de depreciação (FastAPI/TestClient e `datetime.utcnow()` em
writing), sem falha.

## Auditoria Codex

**Auditoria Codex: aprovada em 2026-07-27.**

- As métricas contam fatos, não referências, e não fabricam denominadores.
- `partial` permanece histórico e não substitui a visão corrente.
- Conflito e precedência são somente entradas declaradas.
- O diagrama final representa `source_bundles` como dual-write dos produtores
  suportados, não como passagem obrigatória de todo bronze.
- Limitações de `match_chunks`, consolidado e atores incompletos permanecem
  explícitas.
- Gate independente RT04: `171 passed`, Ruff limpo e `git diff --check` limpo.
