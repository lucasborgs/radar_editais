# RT05-T04 — Serviço de revisão e projeção temporal

**Data:** 2026-07-29
**Branch:** `codex/radar-data-trust-05-t04`
**Base:** `2febd97a5`
**Commit de implementação:** `7abc6549e`
**Commit de correções da auditoria:** `0229b996f`
**Auditoria Codex: pendente**

---

## Resumo

A T04 implementa a decisão humana append-only e uma única projeção temporal
interna. O serviço combina o fato produzido, a exceção exata e uma revisão
válida da mesma fingerprint sem modificar bundle, bronze, silver, gold,
`entities` ou a saída histórica do produtor.

Não foram criados API, tela, migration, cron, backfill ou enforcement em
consumidores.

## Decisões

| Decisão | Validação | Projeção |
|---|---|---|
| `confirm` | Preserva o valor produzido; exige valor recuperável e evidência versionada já ligada à exceção | Data ISO → `fixed`, active/closed conforme `as_of`; status fechado sem prazo → `unknown/closed` |
| `correct` | Exige data ISO `YYYY-MM-DD` e evidência versionada já ligada | `fixed`, active/closed conforme `as_of`; `overridden=true` |
| `confirm_continuous` | Somente `field_path=deadline`; exige evidência versionada já ligada com `quote` não vazia | `continuous/active`, sem fabricar prazo; `overridden=false` |
| `mark_unknown` | Não aceita valor corrigido | `unknown/needs_review`; não vira contínuo nem ativo |

`confirm` de status aberto sem prazo é rejeitado. URL, quote ou hash isolado
não cria nova autoridade: toda evidência selecionada deve corresponder a um
`EvidenceRef` já persistido na exceção corrente. Nova evidência documental deve
entrar primeiro pelo bundle ou produtor aplicável.

Documento e hash sem trecho não provam continuidade. O serviço exige `quote`
não vazia, mas não interpreta seu conteúdo por keyword, regex semântica, LLM
ou rede. Uma futura evidência estruturada sem quote exigirá contrato próprio.

## Persistência e retry

O comando `review_temporal_exception` executa:

```text
carregar exceção exata
  → validar campo, fingerprint, decisão, valor e evidências
  → append_review
  → mark_exception_resolved
  → derivar projeção temporal
```

A revisão é persistida antes da transição `open → resolved`. Exceções
`superseded` não recebem revisão. `mark_exception_resolved` é idempotente para
um registro já resolvido e nunca transiciona `superseded`.

Se o append concluir e a resolução falhar, o retry com o mesmo `review_id`
reaproveita a revisão append-only, valida o mesmo payload e conclui a resolução
sem duplicar ou alterar o histórico.

Falhas reais usam `DataQualityStorageError` sanitizado. Logs contêm somente
categoria e IDs, sem justificativa, evidência, valor bruto ou mensagem de
provedor.

## Read model único

`TemporalValidityProjection` contém:

- `temporal_mode`;
- `validity_state`;
- `value`;
- `input_fingerprint`;
- `exception_id`;
- `review_id`; e
- `provenance`.

`project_temporal_validity` é a única função de projeção corrente:

- fato coerente sem exceção usa avaliação temporal determinística;
- Finep/Eureka (`ABERTA`, sem prazo) permanece `unknown/needs_review`;
- exceção aberta permanece `needs_review`, mesmo após append parcial;
- revisão resolvida só é aplicada quando referencia a exceção corrente e a
  fingerprint coincide;
- fingerprint nova não herda revisão anterior;
- exceção/revisão ausente, inválida ou com falha de leitura retorna
  conservadoramente `needs_review`; e
- falha de leitura nunca concede `active`.

Sem revisão humana, tanto a projeção coerente quanto a conservadora preservam
por identidade a `original_provenance` recebida, sem modificá-la. Se o chamador
não fornecer proveniência original, `provenance` permanece `None`; o read model
não fabrica um produtor temporal. Uma revisão válida substitui apenas a
proveniência da projeção pela proveniência humana abaixo.

`as_of` permanece injetável. O default usa a data corrente em
`America/Sao_Paulo`.

## Proveniência humana

A projeção revisada cria `FactProvenance` somente no read model:

- `producer.kind = human`;
- `producer.name = "data_quality_review"`;
- `producer.version = "data_quality_review:v1"`;
- `review` preserva `review_id`, `actor_id`, `reviewed_at` e override;
- `overridden=true` somente para `correct`;
- evidências são as referências reais já ligadas à exceção;
- derivação referencia exceção, fingerprint e valor anterior;
- estado `stated` para confirmação, correção e continuidade sustentada; e
- estado `unknown` para `mark_unknown`.

O `FactProvenance` original e o salvo no gold não são alterados.

## Arquivos

- `src/radar/core/services/data_quality_reviews.py`
- `src/radar/core/services/data_quality_exceptions.py`
- `tests/unit/test_data_quality_reviews.py`
- `tests/unit/test_temporal_validity_projection.py`
- `docs/execution/radar-data-trust/reports/05-exception-review/RT05-T03-report.md`
- `docs/execution/radar-data-trust/reports/05-exception-review/RT05-T04-report.md`

## Testes

Os dois arquivos focais contêm 46 testes herméticos:

- 28 em `test_data_quality_reviews.py`;
- 18 em `test_temporal_validity_projection.py`.

Cobertura:

- quatro decisões e confirmação de status fechado;
- rejeição de aberto sem prazo, valor corrigido inválido e evidência ausente
  ou não ligada;
- bloqueio de exceção `superseded`;
- ordem append → resolved;
- retry após falha parcial sem revisão duplicada;
- proveniência humana, autoria, override, evidência e derivação;
- proveniência original preservada por igualdade e identidade nos caminhos
  normal e conservador, sem mutação ou fabricação quando ausente;
- Finep/Eureka, exceção aberta e `mark_unknown` fail-closed;
- correção limitada à projeção;
- continuidade aceita somente com `EvidenceRef` ligada, versionada e com
  `quote` não vazia; documento/hash ou evidência genérica não bastam;
- fingerprint nova sem herança;
- leitura ausente ou falhando sem concessão de `active`;
- transição mínima do repositório com fake Supabase; e
- logs e erros sem vazamento de conteúdo bruto.

Validação:

```text
pytest dos 6 arquivos do gate  → 314 passed
ruff check nos 4 arquivos Python alterados → pass
git diff --check 2febd97a5..HEAD → pass
```

## Invariantes

1. Revisões existentes nunca são atualizadas ou removidas.
2. O valor produzido permanece imutável; correção existe apenas na projeção.
3. Continuidade exige evidência ligada e versionada com `quote` não vazia.
4. Ausência de prazo não prova continuidade.
5. Fingerprint material nova exige nova avaliação.
6. Falha operacional não vira certeza factual.
7. Nenhum teste ou serviço modifica `entities` ou gold.

## Limitações

- Serviço interno, sem API ou interface administrativa.
- Nenhum consumidor usa a projeção ainda; enforcement permanece fora da T04.
- Não há aquisição de nova evidência, rede, LLM ou inspeção de payload bruto.
- Não há backfill nem revisão automática do catálogo.
- T05 não foi iniciada.

## Histórico

- `2febd97a5` — base aprovada da RT05-T03.
- `7abc6549e` —
  `feat(data-trust): add temporal review projection service`.
- `0229b996f` —
  `fix(data-trust): preserve provenance and require continuous quote`.
