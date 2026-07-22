# RT00-T03 — Classificadores em shadow

**Status:** `implemented_awaiting_diagnostic`
**Plano:** [`RT00-T03-shadow-classifiers.md`](../../plans/00-relevance/RT00-T03-shadow-classifiers.md)
**Branch/base:** `codex/radar-data-trust-00-t03` / `1566a3972`

## Commits

| Commit | Assunto |
|---|---|
| `e03fbb401` | implementação inicial dos classificadores shadow |
| `9a9635d41` | suíte diagnóstica `relevance_shadow` |
| `5b2b422f4` | relatório inicial da RT00-T03 |
| `24aae951c` | definição de `failed_codes` na spec e no plano |
| `c587eb249` | conclusão do contrato, classificadores, métricas e testes |

## Resultado implementado

### Isolamento shadow

O classificador novo não está conectado a `discover_opportunities()`, `_triage`,
staging, ledger, cache negativo, promoção, API ou gold. Ele só é executado pela
suíte diagnóstica. A RT00-T04 não foi iniciada.

### Cinco classificadores

`src/radar/core/ingestion/relevance_classifier.py` expõe funções independentes
para oportunidade, investidor, ICT, programa e agência. Cada tipo possui prompt
próprio; apenas transporte, parser, validação e dispatch são compartilhados.

O transporte usa `radar.core.llm.llm_client.make_client`, parsing JSON estrito e
os modelos de `radar.domain.relevance`. Fence Markdown, campo extra, código
inválido, `kind` incorreto ou invariante violada produzem erro operacional.

Categorias sanitizadas:

- `parse_failure`;
- `timeout`, incluindo timeout real do SDK OpenAI;
- `provider_error`;
- `contract_violation`; e
- `grounding_error`.

Nenhuma categoria inclui resposta integral, header, credencial ou mensagem
bruta do provedor. Erro nunca fabrica `out_of_scope`.

### Contrato de atores com `failed_codes`

Para Investidor, ICT, Programa e Agência:

- `reason_codes` registra critérios comprovadamente satisfeitos;
- `failed_codes` registra critérios comprovadamente falsos;
- `missing_information` registra critérios ainda ausentes ou ambíguos.

Os campos usam o enum específico do `kind` e são disjuntos. `in_scope` exige
todos os critérios e nenhum `failed_code`; `out_of_scope` exige identidade
comprovada e ao menos um `failed_code` não identitário; `needs_review` não aceita
`failed_codes` e exige ao menos um critério obrigatório explicitamente pendente.

Todo código confirmado ou falho exige uma `evidence` com o mesmo `code` e quote
literal não vazia. A quote é validada como substring do material fornecido.

Os sete goldens de atores receberam explicitamente `failed_codes: []`, sem
alterar decisão, evidência, revisão humana ou IDs.

## Suíte `relevance_shadow`

Registrada no harness unificado como `diagnostic`, sem `Criterion` e sem
threshold bloqueante.

### Corpus

- 14 casos owner-reviewed;
- 7 fontes `src:*`;
- 6 `legacy_triage_case`;
- 1 `curated_record` (KPTL);
- `triage-tavily-093` usa o snapshot oficial `src:*`.

IDs esperados:

```text
triage-tavily-082
triage-tavily-093
triage-dou-000
triage-tavily-084
triage-tavily-118
triage-tavily-079
triage-tavily-098
investidor:indicator-capital
investidor:kptl
ict:embrapii:senai-cimatec
programa:pipe-fapesp
programa:centelha
agencia:finep
agencia:fapesp
```

### Métricas por item

- `decision_accuracy`;
- `reason_code_coverage`;
- `reason_code_precision`;
- `fn_guard` — 1 significa ausência de falso negativo, 0 significa FN;
- `evidence_grounding`;
- `failed_code_exact_match`; e
- `operational_error`.

Erros operacionais produzem `None` nas métricas de qualidade e não são contados
como falsos negativos. Predição vazia reduz coverage a zero e deixa precision
indefinida, evitando inflação artificial. Os run evaluators agregam por `kind` e
registram IDs de divergências, falsos negativos e erros.

## Validação reproduzida

```bash
PYTHONPATH=src pytest -q \
  tests/unit/test_relevance.py \
  tests/unit/test_relevance_goldens.py \
  tests/unit/test_relevance_shadow.py \
  tests/unit/test_hardening_pr4.py \
  tests/unit/test_opportunity_discovery_cache.py
# 280 passed

ruff check src/radar/domain/relevance.py \
  src/radar/core/eval/relevance_goldens.py \
  src/radar/core/ingestion/relevance_classifier.py \
  src/radar/core/eval/relevance_shadow.py \
  src/radar/core/eval/registry.py \
  tests/unit/test_relevance.py \
  tests/unit/test_relevance_goldens.py \
  tests/unit/test_relevance_shadow.py
# All checks passed
```

Também confirmados:

- `git diff --check` limpo;
- loader íntegro;
- 14/14 casos com `human_reviewed=true`;
- manifesto `approved`;
- `data/evaluation/golden/triage.json` intacto; e
- nenhum wiring com o runtime produtivo.

## Limitações honestas

O corpus atual não contém ator `out_of_scope`. Os testes sintéticos comprovam o
contrato de `failed_codes`, mas ainda não medem recall positivo dessa decisão em
casos reais. A expansão representativa pertence à evolução dos goldens e às
métricas da RT00-T06; nenhum threshold pode ser inferido desta amostra.

A run com LLM real ainda não foi executada. Ela requer autorização explícita
porque envia os 14 materiais versionados — incluindo o registro curado da KPTL —
ao provedor configurado. Com autorização:

```bash
python -m radar.core.eval run relevance_shadow
```

## Auditoria Codex

**Veredito técnico:** `aprovado`

**Run externa:** `pendente de autorização`

- implementação e isolamento shadow revisados;
- 280 testes e Ruff reproduzidos;
- erros métricos da implementação interrompida corrigidos;
- relatório anterior, que declarava aprovação e IDs incorretos, substituído;
- RT00-T04 não iniciada.
