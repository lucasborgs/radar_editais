# RT01-T03 — Resolvedor de evidência

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T03-evidence-resolver.md`](../../plans/01-provenance/RT01-T03-evidence-resolver.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t03` / `ae009ddf0`
**Commits:** nenhum — mudanças em staging (`git add`), commit fica para depois da auditoria da governança
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

- `src/radar/core/kg/evidence_resolver.py` — módulo PURO (sem I/O de rede,
  banco ou disco; blocos silver chegam por parâmetro) com:
  - `resolve_quote(quote, blocks, *, source, edital_id, native_id,
    silver_source_hash, canonical_content_hash, source_url, collected_at)` →
    `ResolveResult(candidates, ambiguous, evidence_ref, missing_hash)`;
  - `EvidenceCandidate` — uma ocorrência (`doc`, `page`, `block_idx`,
    `section_path`), imutável;
  - casamento textual por substring exata após normalização determinística
    de whitespace (`_normalize_ws`: colapsa runs de espaço/quebra em espaço
    único + strip) — nenhuma outra normalização (sem case-fold, sem remoção
    de acento, sem regex heurística);
  - candidatos sempre ordenados deterministicamente por
    `(doc, page is not None, page, block_idx)` (`None` de página ordena
    antes de qualquer inteiro);
  - `_classify(...)` implementa a regra do prefixo comum não ambíguo (ver
    "Divergências e decisões");
  - hashes: o chamador informa `silver_source_hash`/`canonical_content_hash`
    já prefixados com o algoritmo real (`"md5:<hex>"`, já que tanto
    `structurer._source_hash` quanto `source_docs.canonical_hash` são md5);
    o módulo nunca re-hasheia nem fabrica hash ausente — sem nenhum dos
    dois, `evidence_ref=None` e `missing_hash=True` no resultado, mesmo que
    a resolução textual tenha sido bem-sucedida e não ambígua;
  - `EvidenceRef.quote` sempre preserva o texto original do chamador, nunca
    a forma normalizada usada para o casamento;
  - sem score numérico de confiança; sem estado factual novo.
- `tests/unit/test_evidence_resolver.py` — 12 testes herméticos (sem
  rede/banco):
  1. trecho único e exato na fixture real `tests/fixtures/gold_equivalence/silver/structured_docs/finep/602.jsonl`
     → `exact` com `doc`/`page`/`block_idx`/`section_path` corretos;
  2. trecho repetido em dois blocos da mesma página (sintético) → `exact`
     só com `page`;
  3. trecho repetido em duas páginas do mesmo doc (sintético) →
     `document_only`;
  4. trecho repetido em documentos diferentes (sintético, adversarial) →
     `unresolved` com `candidates` completos dos dois docs — nunca `exact`;
  5. trecho ausente → `unresolved`, quote preservado, zero coordenadas;
  6. bloco HTML sem `page` (sintético), ocorrência única → `exact` com
     `block_idx`, `page` permanece `None` (nunca fabricado);
  7. repetição em blocos diferentes todos com `page=None` (sintético) →
     `document_only`, não um `exact` fabricado sem coordenada real;
  8. whitespace divergente (quote com quebras de linha vs. bloco com
     espaços simples) → resolve e preserva o quote original no
     `EvidenceRef`;
  9. normalização proibida (case-fold) não acontece: quote em CAIXA ALTA
     não casa com o texto real (misto) → `unresolved`;
  10. round-trip: todo `EvidenceRef` emitido passa por
      `model_dump(mode="json")` → `EvidenceRef.model_validate(...)` e volta
      idêntico;
  11. hash ausente: sem `silver_source_hash`/`canonical_content_hash`,
      `evidence_ref=None` e `missing_hash=True` mesmo com resolução textual
      bem-sucedida e não ambígua;
  12. determinismo: duas chamadas idênticas produzem `candidates`,
      `ambiguous` e `evidence_ref` idênticos.

  Material principal: fixture silver real de RT01-T02 (`finep/602`, usada
  em 6 dos 12 testes). Blocos sintéticos mínimos, claramente rotulados como
  tal em comentário no arquivo, cobrem os cenários que a fixture real não
  contém (repetição controlada em blocos/páginas/documentos e blocos sem
  `page`) — confirmado por grep antes da escrita dos testes que nenhuma
  fixture real de `tests/fixtures/gold_equivalence/silver/` possui blocos
  com `page=None`.

## Divergências e decisões

- **Regra do prefixo comum não ambíguo**, exatamente como especificada no
  enunciado, com um refinamento necessário para satisfazer o invariante do
  T01:
  - mesmo doc + mesma página + mesmo bloco (1 candidato, ou N candidatos no
    mesmo `(doc, page, idx)`) → `exact` completo (`page`, `block_idx`,
    `section_path`);
  - mesmo doc + mesma página, blocos diferentes → `exact` só com `page`
    (sem `block_idx`/`section_path`);
  - apenas mesmo doc (páginas diferentes) → `document_only`;
  - docs diferentes ou nenhuma ocorrência → `unresolved`, zero coordenadas,
    quote preservado.
  - **Refinamento**: quando "mesma página" é `None` uniformemente em todos
    os candidatos (HTML sem unidade paginada) e os blocos diferem, isso não
    é uma coordenada real — é ausência uniforme de página. Produzir
    `exact` com `page=None`/`block_idx=None`/`section_path=[]` violaria o
    invariante do T01 (`locator_quality=exact requires at least one
    resolved coordinate`, em `provenance.py:146-153`). Nesse caso o
    resultado cai para `document_only` em vez de tentar um `exact` sem
    nenhuma coordenada resolvida. Documentado no docstring do módulo e
    coberto pelo teste 7 acima. Este é o único ponto onde a implementação
    vai além da enumeração literal do enunciado — por necessidade de
    contrato, não por escolha de produto; nenhum outro caso da enumeração
    foi alterado.
  - Um segundo refinamento simétrico: para `unresolved` com docs
    diferentes, o campo `document` do `EvidenceRef` fica `None` (nunca
    `candidates[0].doc`) — nomear um documento entre vários candidatos
    ambíguos seria escolher silenciosamente um deles, o que a spec proíbe
    explicitamente ("nunca escolher silenciosamente a primeira ocorrência").
    Esse bug foi pego pelo próprio teste adversarial (caso 4) antes da
    entrega — ver "Validação".
- **Limitação v1 documentada, não mascarada**: um `quote` que atravessa
  fronteira de blocos (começa em um bloco, termina no seguinte) NÃO é
  resolvido nesta versão. Cada bloco é casado isoladamente contra o quote
  completo; um quote que só existe como concatenação de dois blocos produz
  0 candidatos e cai em `unresolved`. Registrado no docstring do módulo
  (seção "Limitação v1"). Resolver merges de blocos adjacentes fica para
  uma iteração futura, se a spec/plano de uma task posterior exigir —
  fora do escopo desta task (que cobre resolução por bloco, não
  reconstrução de texto cross-block).
- **Sem score numérico de confiança**: `ResolveResult`/`EvidenceCandidate`
  não carregam nenhum campo de confiança; a única saída estruturada além
  de `candidates`/`ambiguous`/`evidence_ref` é `missing_hash` (booleano
  sobre disponibilidade de hash, não confiança).
- **Não foi necessário spike**: a ambiguidade é resolvida inteiramente por
  comparação de chaves `(doc, page, block_idx)` — sem heurística de
  algoritmo a comparar, então o gatilho de "só usar spike se a ambiguidade
  exigir comparar algoritmos" (plano RT01-T03) não se aplicou.
- Nenhuma migration, banco, rede ou LLM real — confirmado por inspeção do
  módulo (zero imports de `radar.core.infra`, `psycopg`, `requests`,
  `httpx` ou clientes LLM) e pela execução hermética dos testes.

## Dados e migrations

- Não aplicável — nenhuma migration, tabela ou dado de `data/`/fixtures foi
  alterado. Os testes leem `tests/fixtures/gold_equivalence/silver/` como
  leitura apenas (nenhuma escrita).

## Validação

| Comando/verificação | Resultado |
|---|---|
| `PYTHONPATH=src pytest -q tests/unit/test_evidence_resolver.py` | `12 passed` |
| `PYTHONPATH=src pytest -q tests/unit/test_provenance.py tests/unit/test_gold_equivalence.py` | `73 passed` (57 + 16, baseline preservado) |
| `PYTHONPATH=src pytest -q tests/unit/test_evidence_resolver.py tests/unit/test_provenance.py tests/unit/test_gold_equivalence.py` (conjunto) | `85 passed` |
| `ruff check src/radar/core/kg/evidence_resolver.py tests/unit/test_evidence_resolver.py` | `All checks passed!` (1 `F401` de import não usado corrigido antes desta rodada) |
| `git diff --check` | limpo |
| `git diff ae009ddf0 --cached --diff-filter=MD` | vazio (nenhum arquivo existente modificado ou removido) |
| `git diff ae009ddf0 --cached --stat` | 2 arquivos, ambos novos (`A`), 601 inserções, 0 deleções, 0 modificações |

Durante a escrita dos testes, um teste falhou na primeira execução por bug
real na implementação (não no teste): `EvidenceRef.document` estava sendo
preenchido com `candidates[0].doc` mesmo em `unresolved` com docs
diferentes — corrigido antes da entrega (ver "Divergências e decisões").
Outras 3 falhas iniciais foram de fixtures sintéticas mal construídas
(quote com casing que só casava em um dos dois blocos repetidos
pretendidos) — corrigidas no próprio arquivo de teste, não na
implementação.

`git status --short`:
```
A  src/radar/core/kg/evidence_resolver.py
A  tests/unit/test_evidence_resolver.py
```

`git diff ae009ddf0 --cached --stat`:
```
 src/radar/core/kg/evidence_resolver.py | 222 +++++++++++++++++++
 tests/unit/test_evidence_resolver.py   | 379 +++++++++++++++++++++++++++++++++
 2 files changed, 601 insertions(+)
```

Worktree limpo confirmado: nenhum arquivo rastreado pré-existente aparece
no diff (só `A`, zero `M`/`D`); todas as mudanças estão em staging
(`git add`), nenhum commit foi criado.

## Pendências

- Nenhuma dentro do escopo desta task. Fora de escopo, para tasks
  seguintes:
  - resolução de quote cross-block (limitação v1 documentada acima) —
    revisitar apenas se um caso real da spec/plano futuro exigir;
  - `RT01-T04` (migration aditiva `provenance jsonb`) e `RT01-T05`
    (vertical slice FINEP em dual-write) são quem efetivamente vai chamar
    `resolve_quote` a partir de um produtor real; este módulo só entrega o
    resolvedor puro, sem nenhum caller produtivo ainda.

## Auditoria (governança — Fable)

**Veredito:** aprovada em 2026-07-23.

Validação independente do diff e do comportamento, sem confiar no resumo do
implementador:

- `evidence_resolver.py` e `test_evidence_resolver.py` lidos integralmente;
  diff 100% aditivo confirmado (`git diff ae009ddf0 --diff-filter=MD`
  vazio); nenhum módulo existente ou fixture da T02 tocado;
- testes reexecutados: 85 passed (12 novos + 57 provenance + 16
  equivalence); Ruff e `git diff --check` limpos;
- sondas adversariais próprias, fora da suíte:
  - quote atravessando fronteira de blocos → `unresolved` com 0 candidatos
    (limitação v1 honesta, não mascarada);
  - acentos NÃO são normalizados (`inovacao` ≠ `inovação` → `unresolved`),
    confirmando que só whitespace é tolerado;
  - ordem de entrada dos blocos invertida → resultado idêntico
    (candidatos reordenados deterministicamente);
  - a correção do implementador em `unresolved` multi-doc (documento
    forçado a `None` em vez de `candidates[0].doc`) foi verificada — sem
    escolha silenciosa de documento;
- a exceção do prefixo comum para `page=None` uniforme (HTML repetido →
  `document_only`, nunca `exact` sem coordenada) foi revisada e aceita como
  a leitura correta do invariante T01;
- **edge documentado, não material:** bloco silver malformado sem `doc`
  que caia em `document_only` faz o validador do T01 levantar
  `ValidationError` (falha ruidosa). Comportamento aceito
  deliberadamente: um guard que degradasse para `unresolved` mascararia
  silver corrompido, contrariando "falha parcial visível". Silver real
  sempre carrega `doc` (structurer). Se a T05 encontrar esse caso em dado
  real, é sinal de bug de aquisição — deve subir, não ser absorvido.
