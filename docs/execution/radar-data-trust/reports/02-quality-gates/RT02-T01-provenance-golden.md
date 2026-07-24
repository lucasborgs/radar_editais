# RT02-T01 — Golden representativo de proveniência

**Status:** `passed`
**Plano:** [`plans/02-quality-gates/RT02-T01-provenance-golden.md`](../../plans/02-quality-gates/RT02-T01-provenance-golden.md)
**Branch/commit-base:** `codex/radar-data-trust-02-t01` / base `37f34a74d`
**Commits:** nenhum — mudanças em staging (`git add`), commit fica para depois da auditoria da governança
**Implementador/modelo:** claude-sonnet, worktree isolado

## Realizado

- `data/evaluation/golden/provenance/provenance.json` — lista de 6 casos
  (formato `case_id`/`case_type`/`description`/`input`/`expected_output`/
  `metadata`, espelhando `data/evaluation/golden/extraction.json`), um por
  tipo obrigatório da spec `radar-data-trust-02-quality-gates.md` §7.1:
  1. **`provenance-01-unique-exact`** — trecho único sobre `counterpart`
     (mecanismo, spec 01 §3.1), bloco real `finep/602.jsonl` idx 61 →
     `locator_quality=exact` com `page=6`/`block_idx=61`;
  2. **`provenance-02-repeated-two-pages`** — `"http://www.finep.gov.br/"`
     ocorre verbatim em 2 blocos reais do mesmo doc (idx 56/pág 6, idx
     88/pág 10) → `document_only` (o resolver não escolhe página
     silenciosamente);
  3. **`provenance-03-html-no-page`** — blocos sintéticos mínimos com
     `page=None` (adapter web html_clean, sem unidade paginada) sobre
     `deadline` (temporal, §3.1); trecho em 2 blocos diferentes → cai no
     fallback especial de `evidence_resolver._classify` (page=None
     uniforme não é coordenada real) → `document_only`, nunca `exact` sem
     coordenada;
  4. **`provenance-04-normalized-value`** — `funding_amount` (financeiro,
     §3.1), bloco real idx 49: quote verbatim `"R$600.000,00 (seiscentos
     mil reais)"` ≠ valor normalizado esperado `600000.00`
     (`metadata.normalized_value`) — `exact`, faithfulness sobre o texto
     real do bloco, não sobre o número;
  5. **`provenance-05-absent-field`** — `trl_range` (aderência, §3.1),
     buscado (`"TRL"`) em 31 blocos reais (idx 0–30, escopo
     temático/elegibilidade completo do FINEP 602) e genuinamente ausente
     → 0 candidatos, `unresolved`, `fact_state=absent` (buscado e não
     encontrado — distinto de `unknown`);
  6. **`provenance-06-legacy-no-silver`** — registro legado ilustrativo,
     `blocks=[]`, nenhum hash → `missing_hash=True`, `evidence_ref=None`,
     `fact_state=unknown` (material insuficiente, não busca frustrada). A
     ausência total é o gabarito.
- `data/evaluation/golden/provenance/manifest.json` — espelha
  `golden/relevance/manifest.json`: `review_status="pending"`,
  `case_ids`, `corpus_stats` (6 casos, 4 reais + 2 sintéticos), notas sobre
  proveniência dos blocos e sobre `conflicting`/retificação ficarem fora
  (encaminhados à spec 04, conforme §7.1 e o plano).
- `tests/unit/test_provenance_golden_loads.py` — teste de carga/fidelidade
  (22 casos de teste, 5 classes): estrutura do golden (arquivos existem,
  6 casos, IDs únicos, manifesto consistente, um caso por tipo exigido);
  fidelidade ao caminho real (chama `resolve_quote` de fato para cada caso
  e compara candidatos/`ambiguous`/`missing_hash`/`locator_quality`/
  `evidence_ref` byte a byte com o `expected_output` gravado); invariantes
  de `EvidenceRef`/`FactState` (reconstrói o `EvidenceRef` esperado via
  schema pydantic real, não reimplementa a validação); regras "pare" do
  plano (caso legado sem hash/evidence_ref, caso ausente sem coordenada
  fabricada, caso HTML nunca reporta `exact`, quote normalizado é o texto
  real); faithfulness (quote é substring verbatim de algum bloco de
  input). Não registra nenhuma `Suite` nem toca `registry.py` — só valida
  a fixture.

## Divergências e decisões

- **Todos os 6 `expected_output` foram gerados chamando `resolve_quote`
  de verdade** (script ad-hoc, descartado após uso — não commitado) em vez
  de transcritos à mão, para eliminar risco de o golden divergir do
  comportamento real do resolvedor. O teste de carga refaz essa mesma
  chamada e compara byte a byte — o golden não pode silenciosamente
  dessincronizar do código sem quebrar o teste.
- **Blocos inline, não referência a arquivo**: por instrução explícita do
  plano, cada caso embute os blocos silver relevantes diretamente no JSON
  (não referencia `tests/fixtures/gold_equivalence/...` por caminho). Para
  os casos 1/2/4 (mecânica pontual do locator) incluí só os blocos
  estritamente necessários (1 ou 2), verificando via script que a
  ocorrência é única/dupla nos 121 blocos do documento completo — incluir
  só os blocos relevantes é comportamentalmente idêntico a incluir o
  documento inteiro (o algoritmo ignora blocos sem match), mas evita
  duplicar ~27k tokens de fixture 3 vezes.
- **Caso 5 (campo ausente) usa 31 blocos, não 1 nem 121**: para que
  `fact_state=absent` seja uma alegação honesta ("buscado e não
  encontrado"), o subconjunto precisa representar um escopo de busca real,
  não trivial. Escolhi idx 0–30 do FINEP 602 (introdução, objetivo, áreas
  temáticas, arranjo institucional/elegibilidade) — o lugar onde TRL
  apareceria se estivesse declarado. Confirmei por grep que `TRL` e
  `maturidade tecnol` não ocorrem em nenhum dos 121 blocos do documento
  completo, não só no subconjunto incluído.
- **Campo crítico do caso 5 (`trl_range`)**: mapeado ao grupo "aderência"
  de spec 01 §3.1 (`trl_range` está literalmente na tabela), e a ausência
  reflete um padrão real já visível em `data/evaluation/golden/
  extraction.json` (`trl_range` absent em várias entradas FINEP
  existentes) — não inventei um campo fora do escopo autorizado; segui a
  tabela §3.1 como instruído.
- **Casos 2 e 3 não são ancorados a um campo crítico específico**
  (`critical_field=null`): são primariamente demonstrações da mecânica do
  locator (ambiguidade de página / fallback HTML), não de um fato do
  §3.1/§3.2. Documentado explicitamente em `metadata.critical_field_note`
  do caso 2; o caso 3 foi ancorado a `deadline` (temporal, §3.1) porque o
  texto sintético fala de prazo de inscrição, mas o ponto central do caso
  é a regra do locator, não o campo.
- **Casos 3 e 6 usam blocos/valores sintéticos mínimos** — única exceção
  permitida pelo plano ("blocos sintéticos mínimos só para HTML-sem-página
  e legado"), porque nenhum fixture real em
  `tests/fixtures/gold_equivalence/silver/` tem `page=None` hoje (web,
  fapesp, fapesc — todos verificados, todos paginados) nem representa um
  registro sem bloco algum. Ambos claramente marcados
  `metadata.synthetic=true` e com nota explicando que IDs/quotes são
  ilustrativos, não uma citação real de edital.
- **Nenhuma coordenada/hash/citação fabricada nos casos 5 e 6**: caso 5
  preserva o quote buscado (`"TRL"`) sem página/bloco (locator_quality
  real é `unresolved`, não inventado); caso 6 tem `evidence_ref=None`
  puro (sem hash, sem blocos) — a ausência é o resultado do código real,
  não uma anotação manual.
- **Um único arquivo de casos (`provenance.json`)**, não um arquivo por
  caso: só 6 casos pequenos, mesma escolha estrutural de
  `golden/extraction.json` — menos arquivos, mesma consistência.
- Nenhum módulo novo em `src/radar/core/eval/`, nenhuma linha em
  `registry.py`, nenhum threshold — confirmado por `git diff --cached
  --stat` (só `data/evaluation/golden/provenance/*`,
  `tests/unit/test_provenance_golden_loads.py` e este relatório).

## Dados e migrations

- Não aplicável — nenhuma migration, tabela ou dado de produção tocado.
  Os testes leem apenas os JSONs novos e chamam `resolve_quote` (módulo
  puro, sem I/O); `tests/fixtures/gold_equivalence/silver/...` foi lido
  (não modificado) só para confirmar unicidade/ausência de trechos.

## Validação

| Comando/verificação | Resultado |
|---|---|
| `PYTHONPATH=src pytest -q tests/unit/test_provenance_golden_loads.py` | `22 passed` |
| `PYTHONPATH=src pytest -q tests/unit` | `1343 passed, 2 skipped` (baseline preservado, 0 falhas) |
| `ruff check tests/unit/test_provenance_golden_loads.py` | `All checks passed!` |
| `git diff --cached --check` | limpo |
| `git diff 37f34a74d --stat` (untracked incluído via `git add -A` no golden/teste/relatório) | só arquivos novos: `data/evaluation/golden/provenance/manifest.json`, `data/evaluation/golden/provenance/provenance.json`, `tests/unit/test_provenance_golden_loads.py`, `docs/execution/.../RT02-T01-provenance-golden.md` |

`git status --short` (antes do commit):
```
A  data/evaluation/golden/provenance/manifest.json
A  data/evaluation/golden/provenance/provenance.json
A  tests/unit/test_provenance_golden_loads.py
A  docs/execution/radar-data-trust/reports/02-quality-gates/RT02-T01-provenance-golden.md
```

Worktree limpo confirmado: nenhum arquivo rastreado pré-existente aparece
no diff (só `A`, zero `M`/`D`); nenhum código de suíte, `registry.py` ou
fixture existente tocado.

## Pendências

- Nenhuma dentro do escopo desta task. Fora de escopo, para RT02-T02
  (suíte `provenance` diagnóstica):
  - construir a `Suite` que efetivamente consome este golden e agrega
    taxa de resolução de locator / completude de proveniência por campo
    crítico / faithfulness — este golden é só o insumo/gabarito;
  - decidir se `critical_field=null` (casos 2 e 3) deve contar ou não no
    denominador de "completude de proveniência por campo crítico" da
    suíte — decisão de agregação, não desta task.

## Auditoria Codex

**Veredito:** pendente

- Enumeração dos 6 casos confere 1:1 com spec §7.1 e o plano
  RT02-T01-provenance-golden.md; `conflicting`/retificação conscientemente
  fora de escopo (nota no manifesto);
- `expected_output` de cada caso não foi transcrito à mão — foi gerado
  chamando `resolve_quote` real e o teste de carga refaz a mesma chamada
  e compara byte a byte (`TestResolveQuoteFidelity`);
- nenhuma coordenada/hash/citação fabricada nos casos 5/6 — confirmado
  pelo teste `TestNoFabrication` e por leitura direta do JSON gerado;
- blocos reais reusados de `tests/fixtures/gold_equivalence/silver/
  structured_docs/finep/602.jsonl` nos casos 1/2/4/5; sintéticos mínimos
  só nos casos 3/6, ambos marcados `synthetic=true` com nota explícita;
- nenhum módulo de suíte, entrada em `registry.py` ou threshold
  introduzido — confirmado por `git diff --cached --stat`;
- suíte de testes completa (1343 passed, 2 skipped) sem regressão; Ruff e
  `git diff --check` limpos;
- worktree limpo, nada pushed, commit único pendente de criação após esta
  auditoria conforme instrução do orquestrador.
