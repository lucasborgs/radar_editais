# RT00-T03 — Classificadores em shadow

**Status:** `completed`
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
| `2015551ea` | correção do relatório após auditoria técnica |
| `06effbe70` | clareza das invariantes de saída e grounding após primeira run |

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

## Runs diagnósticas autorizadas

Foram executadas duas runs locais com o provedor configurado, sem publicação no
Langfuse e sem escrita em staging, gold ou produção.

### Primeira run — descoberta de ambiguidade contratual

Artefato local:
`eval_results/20260722_170900_relevance_shadow.json`.

- 14 casos processados;
- `decision_accuracy`: `0.8182` nos 11 casos sem erro;
- `operational_error`: `0.2143` (3 casos);
- 2 violações de contrato em oportunidades `out_of_scope`, pois o prompt não
  tornava explícita a duplicação do código `X*` em `reason_codes` e
  `exclusion_codes`;
- 1 erro de grounding em programa por quote não literal; e
- nenhum falso negativo de oportunidade.

O achado motivou `06effbe70`: a invariante foi explicitada e todos os prompts
passaram a exigir uma única substring literal e contígua por evidência. A regra
de Brasil para agências estaduais também foi reconciliada com o critério já
aprovado, sem alterar enum, golden ou decisão de produto.

### Segunda run — baseline aceito da T03

Artefato local:
`eval_results/20260722_171044_relevance_shadow.json`.

- 14 casos processados;
- `decision_accuracy`: `0.8462` nos 13 casos sem erro;
- `reason_code_coverage`: `0.6923`;
- `reason_code_precision`: `0.9444`;
- `evidence_grounding`: `0.6923`;
- `operational_error`: `0.0714` (1 caso);
- `failed_code_exact_match`: `1.0000`;
- nenhum falso negativo de oportunidade;
- 1 erro de grounding em `triage-tavily-098`; e
- 2 falsos negativos conservadores de atores: `programa:pipe-fapesp` e
  `agencia:fapesp`, ambos classificados como `needs_review` em vez de
  `in_scope`.

As duas runs com temperatura zero não foram idênticas. Essa variação e a baixa
cobertura de códigos em materiais incompletos são sinais diagnósticos reais,
não thresholds nem autorização para calibrar o prompt aos 14 casos. Devem ser
reavaliados com corpus ampliado na RT00-T06.

## Limitações honestas

O corpus atual não contém ator `out_of_scope`. Os testes sintéticos comprovam o
contrato de `failed_codes`, mas ainda não medem recall positivo dessa decisão em
casos reais. A expansão representativa pertence à evolução dos goldens e às
métricas da RT00-T06; nenhum threshold pode ser inferido desta amostra.

O baseline não autoriza integração produtiva por si só. A RT00-T04 deve manter
o novo resultado aditivo e reversível no staging; promoção automática continua
fora do escopo.

## Auditoria Codex

**Veredito técnico:** `aprovado`

**Run externa:** `executada e registrada`

- implementação e isolamento shadow revisados;
- 280 testes e Ruff reproduzidos;
- erros métricos da implementação interrompida corrigidos;
- duas runs reais analisadas, com zero falso negativo de oportunidade na run
  final;
- relatório anterior, que declarava aprovação e IDs incorretos, substituído;
- RT00-T04 não iniciada.
