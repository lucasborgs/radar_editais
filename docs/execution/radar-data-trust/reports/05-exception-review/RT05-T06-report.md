# RT05-T06 — Interface administrativa de exceções

**Data:** 2026-07-29
**Branch:** `codex/radar-data-trust-05-t06`
**Base:** `b7f436dd9`
**Commit de frontend:** `0e2798d36`
**Worktree:** `/private/tmp/radar-editais-rt05-t06`
Auditoria Codex: aprovada em 2026-07-29.

---

## Resumo

Entreguei a seção administrativa **Exceções de dados** na área da Descoberta,
consumindo exclusivamente a API administrativa da T05. A interface continua
sem qualquer alteração de backend Python, regra temporal, promoção/rejeição ou
novo fluxo editorial.

Esta rodada corrige os achados da auditoria da T06:

- submissões obsoletas agora são descartadas por `submitRequestId`;
- o formulário é encerrado após sucesso e quando o detalhe já traz
  `current_review`;
- perda de autorização limpa lista, detalhe, paginação, modal e estado
  administrativo, preservando apenas a mensagem local de acesso restrito.

---

## Entregas

### 1. Cliente tipado da API

Arquivo atualizado:

- `frontend/src/lib/api.ts`

Adicionei tipos e funções para:

- `GET /data-quality/exceptions`
- `GET /data-quality/exceptions/{exception_id}`
- `POST /data-quality/exceptions/{exception_id}/reviews`

Os tipos espelham os modelos sanitizados da T05:

- `DataQualityExceptionOut`
- `DataQualityExceptionListResponse`
- `DataQualityReviewIn`
- `DataQualityReviewOut`
- `DataQualityEvidenceRef`
- enums/uniões de `subject_kind`, `issue_code`, `state` e `decision`

### 2. Aba administrativa na Descoberta

Arquivo atualizado:

- `frontend/src/app/discovered/page.tsx`

A página agora exibe um switch simples entre:

- `Descoberta`
- `Exceções de dados`

A aba nova não bloqueia a fila editorial existente.

### 3. Painel de exceções

Arquivo novo:

- `frontend/src/components/discovered/AdminDataQualityExceptions.tsx`

A seção nova entrega:

- lista com filtros por estado, código, fonte e campo;
- paginação simples com `has_more` e `next_offset`;
- modal de detalhe com sujeito, fonte, campo, valor seguro, impacto, estado
  e evidências versionadas;
- formulário de revisão com as quatro decisões permitidas;
- seleção apenas de evidências já recebidas;
- bloqueio de `confirm_continuous` sem `quote`;
- atualização local de lista e detalhe após sucesso, sem recarregar a página.

### 4. Correções de auditoria

Arquivo atualizado:

- `frontend/src/components/discovered/AdminDataQualityExceptions.tsx`

Foram corrigidos três comportamentos:

- submissões obsoletas agora usam `submitRequestId` e só alteram estado quando
  ainda pertencem à mesma exceção, ao mesmo formulário e ao mesmo token ativo;
- a revisão bem-sucedida fecha o formulário atual, atualiza `selectedException`
  com a resposta do POST, limpa `form` e `formExceptionId`, e impede reedição
  com o mesmo `review_id`;
- a perda de autorização limpa fila, paginação, detalhe, modal e erros
  anteriores, deixando apenas o estado local `Acesso restrito`.

### 5. Marcações documentais

Arquivo atualizado:

- `docs/execution/radar-data-trust/reports/05-exception-review/RT05-T05-report.md`

Marcação solicitada:

- `Auditoria Codex: aprovada em 2026-07-29`

---

## Contratos utilizados

- Listagem administrativa com `status`, `code`, `source`, `field`, `limit`
  e `offset`.
- Detalhe administrativo com resposta segura da exceção e revisão corrente.
- Revisão administrativa com `review_id`, `decision`, `justification`,
  `corrected_value` e `evidence_refs`.

---

## Comportamento de retry e cancelamento

- O `review_id` é gerado no cliente com `crypto.randomUUID()`.
- O retry reutiliza o mesmo `review_id` somente enquanto a mesma submissão
  falha e o mesmo formulário permanece aberto.
- Após sucesso, o formulário é limpo e a edição com aquele `review_id` é
  encerrada.
- Respostas obsoletas de lista, detalhe e submissão são descartadas por IDs de
  requisição e pelo token ativo.
- Fechar o modal, abrir outra exceção ou perder o token invalida a submissão em
  andamento.

---

## Validação

```bash
cd frontend
npx tsc --noEmit
npm run lint
cd ..

ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q \
  tests/unit/test_data_quality_api.py \
  tests/unit/test_admin_gate.py

git diff --check b7f436dd9..HEAD
```

Resultado:

- `npx tsc --noEmit`: aprovado
- `npm run lint`: aprovado, com avisos preexistentes em `src/app/page.tsx`,
  `src/app/workspace/[sessionId]/page.tsx` e `src/lib/auth.tsx`
- `pytest`: `21 passed`, `1 warning`
- `git diff --check b7f436dd9..HEAD`: aprovado
- `git status --short --branch`: `## codex/radar-data-trust-05-t06`

---

## Limitações e QA manual

- Não houve backend, migration, worker, LLM, rede externa ou mudança em
  `promote/reject`.
- Não foi criado novo framework de teste de frontend.
- QA manual da interação visual fica como acompanhamento natural da aba,
  embora a validação de contrato e tipagem tenha passado.
- T07 não foi iniciada.
- Não houve merge nem push.
- `Auditoria Codex: pendente`

---

## Observação final

A interface segue pequena e conservadora: usa só a API administrativa da T05,
não calcula regra temporal no frontend e preserva a fila editorial existente.
