# RT01-T10 — API e Explorar

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T10-api-explore.md`](../../plans/01-provenance/RT01-T10-api-explore.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t10` / base `1c24994b2`
**Commits:** commit único desta branch (`git log codex/radar-data-trust-01-t10`)
**Implementador/modelo:** deepseek (opencode), worktree isolado

## Realizado

### 1. `src/radar/core/kg/provenance_read.py` — função pura de leitura pública

UMA função, `public_provenance(stored: dict | None) -> dict`, que traduz o JSONB
`entities.provenance` para o subconjunto público definido na spec §6.3:

- cada path mapeia para `{"state": <str>, "citations": [...]}`;
- citations contém SOMENTE `EvidenceRef`s com `locator_quality` `exact` ou
  `document_only` (unresolved → state preservado, sem citação);
- campos operacionais (`producer`, `derivation`, `validations`, `hashes`,
  `review`) NUNCA são expostos;
- `None`/`{}`/malformado → `{}` (nunca levanta, nunca inventa estado);
- path ausente no dict público = unknown/legado (contrato documentado no
  docstring).

### 2. `src/radar/core/kg/entity_catalog.py` — chave aditiva `provenance`

As fichas completas ganharam a chave ADITIVA `"provenance"`:

- `_row_to_card` (usada por `get_edital` e `get_opportunity` para editais);
- `_curated_card` (usada por `get_opportunity` para programas/investidores);
- `get_investidor` (ficha crua do fundo);
- `get_programa` (ficha crua do programa).

Nenhuma chave existente mudou de nome, tipo ou valor. Registros legado sem
`provenance` na row retornam `"provenance": {}`.

### 3. `src/radar/core/llm/agent_tools/explore_tools.py` — provenance no payload factual

As tools factuais `get_edital` e `get_investidor` passam a incluir um bloco
`[PROVENANCE:<path>]` formatado no payload textual que o agente recebe, com
state e citações públicas. A verbalização na superfície é T11 — apenas a
disponibilidade dos dados foi implementada.

### 4. `tests/unit/test_provenance_read.py` — 20 testes herméticos

Cobertura:
- `public_provenance`: stated+exact, unresolved, document_only, inferred sem
  refs, {} / None / malformado, adversarial (producer nunca vaza);
- entity_catalog: `_row_to_card` com provenance, legado, snapshot antes/depois;
  `_curated_card` com provenance e legado; `get_investidor` e `get_programa`
  com provenance (monkeypatch de `_fetch_one` + `_client`);
- explore_tools: `get_edital` e `get_investidor` incluem `[PROVENANCE:...]`.

## Divergências e decisões

- Nenhuma divergência significativa. A spec e o plano foram seguidos
  literalmente.
- Os testes de `get_investidor` e `get_programa` exigiram monkeypatch também de
  `_client()` (além de `_fetch_one`) porque a função real `_client()` tenta
  conectar ao Supabase real — padrão consistente com `test_explore_agent.py`.
- A formatação do bloco de proveniência nas tools factuais usa `[PROVENANCE:path]`
  como prefixo para que o agente possa distinguir facilmente state e citações
  no texto. Isso não altera prompts ou instruções do agente — apenas o dado
  que o agente recebe.

## Dados e migrations

Nenhuma migration. As colunas `entities.provenance` e
`entity_relationships.provenance` já existem (RT01-T04). Nenhum índice, flag de
env, cache ou suite de eval nova foi criada.

## Validação

### 1. Suíte nova — hermética (sem rede/banco)

```
$ PYTHONPATH=src pytest -q tests/unit/test_provenance_read.py
....................                                                     [100%]
20 passed in 2.99s
```

### 2. Suíte unitária completa — 100% verde

```
$ PYTHONPATH=src pytest -q tests/unit
1305 passed, 2 skipped, 4 warnings in 16.39s
```

(2 skips pré-existentes, não relacionados a esta task.)

### 3. Gate de equivalência do gold (T02) — intacto

```
$ PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py
................                                                         [100%]
16 passed in 0.52s
```

### 4. Ruff

```
$ ruff check src/radar/core/kg/provenance_read.py \
    src/radar/core/kg/entity_catalog.py \
    src/radar/core/llm/agent_tools/explore_tools.py \
    tests/unit/test_provenance_read.py
All checks passed!
```

### 5. `git diff --check`

```
$ git diff --check
(sem output — sem erros de whitespace)
```

### 6. `git diff 1c24994b2 --stat`

```
$ git diff 1c24994b2 --stat
 src/radar/core/kg/entity_catalog.py             |  6 ++++++
 src/radar/core/llm/agent_tools/explore_tools.py | 27 +++++++++++++++++++++++++
 2 files changed, 33 insertions(+)
```

Arquivos novos (não aparecem no `--stat` acima; incluídos no commit único):
- `src/radar/core/kg/provenance_read.py`
- `tests/unit/test_provenance_read.py`

`git status --short` antes do commit:
```
 M src/radar/core/kg/entity_catalog.py
 M src/radar/core/llm/agent_tools/explore_tools.py
?? src/radar/core/kg/provenance_read.py
?? tests/unit/test_provenance_read.py
```

Nenhum arquivo fora do escopo autorizado foi tocado. Nenhum migration,
RLS, gold.py, provenance_writer, prompt de agente, retriever/embedder/ranking,
ou frontend foi alterado.

## Pendências

- Nenhuma dentro do escopo aprovado. Fora de escopo: verbalização de
  `inferred`/`conflicting` na superfície (T11), backfill e shadow metrics
  (T12), e evals (T13) — tasks posteriores do plano.

## Veredito

**pendente****Veredito:** aprovada em 2026-07-24 (auditoria da governança — Fable).

- Diff inspecionado integralmente; escopo aditivo, zero mudança em chave
  pré-existente de ficha/card; gate T02, gold e migrations intocados;
- `public_provenance` cumpre o contrato à risca: fail-safe (malformado →
  {}/pulado), `unresolved` nunca vira citação pública, e a sonda
  adversarial da governança confirmou que NENHUM campo operacional vaza
  (producer/derivation/validations/review/hashes/coordenadas internas
  ausentes do output por construção — whitelist de 5 campos);
- tools do Explorar: bloco textual [PROVENANCE:path] só com estado +
  documento/página + quote truncado; nenhum prompt/instrução de agente
  alterado — verbalização na superfície permanece na T11;
- **desvio aceito com registro:** a chave `provenance` foi anexada no
  builder compartilhado `_row_to_card`, então as LISTAGENS também a
  carregam (a instrução era só fichas). Aceito pela governança: é a rota de
  menor código, expõe o mesmo subconjunto público sob as mesmas policies, e
  o catálogo pré-beta é pequeno; se o payload de listagem pesar, otimizar
  vira item da T13/backlog — não é bloqueio;
- validação independente: 20/20 no teste novo, suíte tests/unit COMPLETA
  1305 passed/2 skipped, gate 16/16, Ruff e `git diff --check` limpos.

