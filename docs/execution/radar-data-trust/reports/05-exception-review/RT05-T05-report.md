# RT05-T05 — API administrativa de exceções

**Data:** 2026-07-29
**Branch:** `codex/radar-data-trust-05-t05`
**Base:** `463b8be41`
**Commit de correção:** `d8a22934b`
**Worktree:** `/private/tmp/radar-editais-rt05-t05`

---

## Resumo

Entreguei a API administrativa autenticada para a fila única de exceções de
qualidade de dados, com leitura por lista/detalhe e criação de revisão
temporal via `AdminUserId`.

A implementação segue fail-closed, sem frontend, sem migration, sem workflow
novo e sem mudança em promover/rejeitar. O router só orquestra os serviços de
RT05-T02 a T04.

---

## Entregas

### 1. Router administrativo

Arquivo novo:

- `src/radar/api/routers/data_quality.py`

Rotas entregues:

- `GET /data-quality/exceptions`
- `GET /data-quality/exceptions/{exception_id}`
- `POST /data-quality/exceptions/{exception_id}/reviews`

Características:

- todas protegidas por `AdminUserId`;
- `actor_id` vem exclusivamente da autenticação;
- request e response models usam Pydantic com `extra = forbid`;
- paginação simples por `limit` e `offset`, com `limit` máximo de 100;
- filtros suportados: `status`, `code`, `source` e `field`;
- retorno seguro com sujeito, fonte, campo, código, valor seguro, evidências
  versionadas, impacto e estado;
- revisão retorna a projeção atual da exceção, sem expor `actor_id`.

### 2. Integração na API

Arquivo atualizado:

- `src/radar/api/app.py`

O router administrativo de exceções foi registrado no shell da aplicação.

### 3. Repositório de exceções

Arquivo atualizado:

- `src/radar/core/services/data_quality_exceptions.py`

Ampliei a listagem para suportar os filtros do contrato administrativo:

- `status`
- `issue_code`
- `field_path`
- `source` via `evidence_refs.source`
- `limit` e `offset`

Também adicionei uma exceção tipada específica para conflito de revisão:
`DataQualityReviewConflictError`.

### 4. Wiring de auth

Arquivo atualizado:

- `tests/unit/test_admin_gate.py`

Adicionei a verificação de que o novo router também depende de `AdminUserId`.

### 5. Auditoria da T04

Arquivo atualizado:

- `docs/execution/radar-data-trust/reports/05-exception-review/RT05-T04-report.md`

Marcação solicitada:

- `Auditoria Codex: pendente`

---

## Contratos expostos

### Lista

`GET /data-quality/exceptions`

Query params:

- `status`
- `code`
- `source`
- `field`
- `limit`
- `offset`

Resposta:

- `items`
- `limit`
- `offset`
- `has_more`
- `next_offset`

### Detalhe

`GET /data-quality/exceptions/{exception_id}`

Resposta segura com:

- sujeito;
- fonte;
- campo;
- código;
- valor seguro;
- evidências versionadas;
- impacto;
- estado;
- projeção de revisão corrente, quando existir.

### Revisão

`POST /data-quality/exceptions/{exception_id}/reviews`

Campos aceitos:

- `review_id`
- `decision`
- `justification`
- `corrected_value`
- `evidence_refs`

Regras observadas:

- `actor_id` não é aceito no payload;
- `confirm_continuous` e `correct` exigem evidência;
- a revisão não aciona promote/reject editorial;
- retry com o mesmo `review_id` permanece idempotente no serviço;
- colisão material de `review_id` devolve `409` via exceção tipada;
- falha real de storage continua devolvendo `503`.

---

## Sanitização e autorização

- Falhas de storage retornam erro categórico `503`, sem payload bruto,
  traceback, URL sensível ou segredo.
- Colisões de revisão retornam `409` categórico sem interpretar texto de
  storage.
- Erros de validação de revisão retornam `404` ou `422` categóricos, sem
  expor texto interno bruto.
- `source_url` é descartado da serialização segura de evidências.
- O gate é fail-closed: sem autenticação ou sem permissão administrativa, a
  rota não responde a fila.

---

## Validação

Com `ENVIRONMENT=test` e a `.venv` existente do workspace:

```bash
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q \
  tests/unit/test_data_quality_api.py \
  tests/unit/test_admin_gate.py \
  tests/unit/test_data_quality_reviews.py \
  tests/unit/test_data_quality_exceptions.py \
  tests/unit/test_temporal_validity_projection.py

/Users/lucasborges/radar_editais/.venv/bin/ruff check \
  src/radar/api/routers/data_quality.py \
  src/radar/api/app.py \
  src/radar/core/services/data_quality_exceptions.py \
  src/radar/core/services/data_quality_reviews.py \
  tests/unit/test_data_quality_api.py \
  tests/unit/test_admin_gate.py

git diff --check 463b8be41..HEAD
```

Resultado final:

- `pytest` na bateria pedida: `131 passed`
- `ruff check` na lista solicitada: aprovado
- `git diff --check 463b8be41..HEAD`: aprovado

---

## Limitações

- A revisão administrativa segue o contrato temporal já existente em T04;
  não foi criado novo workflow genérico.
- Não houve frontend, migration, worker, rede, LLM ou mudança editorial.
- T06 não foi iniciado.
- Não houve merge nem push.

---

## Observação final

A API permanece pequena e conservadora: só lê a fila, só registra revisão
autenticada e só expõe dados seguros para operação.
