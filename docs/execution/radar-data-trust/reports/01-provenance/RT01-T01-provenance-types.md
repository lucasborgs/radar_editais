# RT01-T01 — Tipos de proveniência

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T01-provenance-types.md`](../../plans/01-provenance/RT01-T01-provenance-types.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t01` / `e78989876b159418c58a44806094f1e635692db1`
**Commits de implementação:** `699053ad3` (contrato inicial), `ff2f555c6` (relatório inicial), `3bc60c14c` (correções de auditoria)
**Implementador/modelo:** Claude (claude-sonnet-5), worktree isolado

## Realizado

### Commit `699053ad3` — contrato de proveniência

- Criado `src/radar/domain/provenance.py` com os tipos puros de domínio
  descritos na spec §4:
  - `FactState` — enum `stated | inferred | absent | conflicting | unknown`
    (estado factual do fato publicado, distinto do `FieldState` legado)
  - `LocatorQuality` — enum `exact | document_only | unresolved`
  - `ProducerKind` — enum `adapter | deterministic | llm | human | default | backfill`
  - `EvidenceRef` — referência estruturada (schema_version, source, native_id,
    edital_id, source_url, document, page, block_idx, section_path, quote,
    canonical_content_hash, silver_source_hash, collected_at, locator_quality)
  - `ProducerInfo`, `DerivationInfo`, `ValidationResult`, `ReviewInfo`
  - `FactProvenance` — composição de `state`, `evidence_refs`, `producer`,
    `derivation`, `validations`, `review`, `updated_at`
  - `evidence_ref_from_extracted()` — adaptador explícito
    `Extracted[T].evidence` → `EvidenceRef`
  - `PROVENANCE_SCHEMA_VERSION = 1`
- Todos os modelos com `model_config = {"extra": "forbid"}` (mesmo padrão de
  `relevance.py`), rejeitando campos desconhecidos.
- Invariantes estruturais (`model_validator`/`field_validator`):
  - `EvidenceRef.page` rejeita valores `< 1` (1-based)
  - `EvidenceRef` exige ao menos um de `canonical_content_hash` ou
    `silver_source_hash`
  - `EvidenceRef` com `locator_quality=unresolved` rejeita `page`,
    `block_idx` ou `section_path` não vazios (coordenadas posicionais
    fabricadas); `quote` continua permitido porque é conteúdo capturado, não
    uma coordenada de posição
  - `FactProvenance` rejeita `producer.kind=default` combinado com
    `state=stated` (regra explícita da spec §4.1)
- `src/radar/domain/__init__.py` atualizado: exporta os **11 símbolos
  novos** (`FactState`, `LocatorQuality`, `ProducerKind`, `EvidenceRef`,
  `ProducerInfo`, `DerivationInfo`, `ValidationResult`, `ReviewInfo`,
  `FactProvenance`, `evidence_ref_from_extracted`, `PROVENANCE_SCHEMA_VERSION`),
  sem alterar nenhum export existente. (Correção de contagem: o relatório
  inicial dizia "10 símbolos novos" listando 11 — a lista estava certa, o
  número não.)
- `src/radar/domain/edital_extraction.py` **não foi alterado** — ver
  "Divergências e decisões" sobre a escolha de compatibilidade.
- Criados 34 testes em `tests/unit/test_provenance.py` (commit inicial),
  ampliados para 65 no commit de correção da auditoria.

### Commit `3bc60c14c` — correções de auditoria

Auditoria final apontou 6 lacunas estruturais na entrega inicial. Todas
corrigidas neste commit, sem iniciar `RT01-T02`:

1. **`EvidenceRef.schema_version` era `int` solto** (aceitava qualquer
   inteiro). Corrigido para `Literal[1] = 1` — `schema_version=1` funciona,
   qualquer outro valor (`0`, `2`, ...) é rejeitado por `ValidationError`.
2. **`LocatorQuality` ganhou consistência semântica própria por valor**,
   antes só `unresolved` tinha invariante:
   - `unresolved`: mantém a regra original — `quote` permitido, `page`/
     `block_idx`/`section_path` proibidos (posição fabricada);
   - `document_only`: **novo** — exige `document` não vazio (nem `None`
     nem string em branco) e proíbe declarar `page`/`block_idx`/
     `section_path` (isso seria `exact`, não `document_only`);
   - `exact`: **novo** — exige ao menos uma coordenada resolvida entre
     `page`, `block_idx` ou `section_path` (não pode ser "exact" sem
     nenhuma posição);
   - `block_idx`, quando presente, agora exige `>= 0` (`field_validator`
     dedicado, mesmo padrão de `page`).
3. **`EvidenceRef.source` era opcional** (`str | None = None`). Corrigido
   para obrigatório e não vazio (`field_validator` rejeita string vazia ou
   só espaços) — toda evidência agora declara explicitamente de onde veio.
4. **`FactProvenance` com `state=stated` não exigia nenhum `EvidenceRef`.**
   Adicionado invariante: `stated` sem `evidence_refs` é rejeitado (spec
   §8.1, validador `state_consistent`: "stated exige evidência"). Conforme
   instrução, **nenhuma regra nova foi criada para `inferred`/`absent`/
   `conflicting`** — os três continuam aceitando `evidence_refs=[]`
   (testado explicitamente).
5. **`ReviewInfo` foi reescrito** de `{reviewer, note, reviewed_at,
   overridden}` (forma especulativa do commit inicial, não pedida pela
   spec) para a referência append-only pedida agora: `review_id: str`
   (obrigatório, não vazio), `actor_id: str` (obrigatório, não vazio),
   `reviewed_at: datetime` (obrigatório), `overridden: bool`. `reviewer` e
   `note` foram removidos. Continua sem armazenamento, migration ou
   exposição em API — é só o tipo.
6. **`evidence_ref_from_extracted`** teve a heurística de
   `locator_quality` default ajustada para ficar consistente com os novos
   invariantes de `exact`/`document_only`: agora considera `section_path`
   (antes só `page`/`block_idx`) na decisão de `exact`, e usa `document`
   truthy (antes `is not None`) para não tratar `document=""` como
   `document_only` válido.

Testes: 40 → 65 (todos passando), cobrindo cada invariante nova em par
positivo/negativo (ver "Validação").

## Divergências e decisões

- **Compatibilidade com `Extracted.evidence`: adaptador explícito, não
  extensão do schema.** A spec (§5.3) permite dois caminhos: adicionar
  `evidence_refs` opcional a `Extracted[T]`, ou um adaptador explícito.
  `Extracted[T]` é genérico e usado em todo `EditalExtraction` — qualquer
  campo novo, mesmo com default, muda o dicionário de `model_dump()` e
  arrisca o requisito "round-trip de payload legado deve continuar válido"
  (comparação de dict passaria a incluir uma chave nova ausente no payload
  original). O adaptador (`evidence_ref_from_extracted`) é a extensão
  aditiva estritamente menor: zero mudança de schema, zero risco a
  `EditalExtraction`, testável isoladamente. `edital_extraction.py`
  permanece byte-a-byte idêntico ao commit-base.
- **`FactState` é um tipo novo, não uma extensão de `FieldState`.** A tarefa
  proibia ampliar o enum legado silenciosamente. `FieldState` é a saída
  binária da extração-LLM para um campo de DECISÃO (`stated/inferred/absent`);
  `FactState` cobre o ciclo completo do fato publicado no gold, incluindo
  `conflicting` e `unknown`, que não existem e não fazem sentido no contrato
  de extração. Os dois tipos não são intercambiáveis por design.
- **CONFIRMADO na auditoria: `quote` não é tratado como "coordenada" sob
  `locator_quality=unresolved`.** A spec (§4.2) diz "unresolved registra
  falha sem fabricar coordenadas exatas". Interpretação mantida:
  coordenadas = posição no documento (`page`, `block_idx`, `section_path`);
  `quote` é conteúdo (o texto capturado), não uma alegação de posição. A
  instrução de correção da auditoria confirmou essa leitura explicitamente
  ("unresolved: pode manter quote, mas não page, block_idx ou
  section_path") — sem ambiguidade remanescente. Essa leitura também é a
  que permite o adaptador funcionar: um `Extracted.evidence` sem
  documento/página conhecidos ainda carrega o texto verbatim legado, e
  `locator_quality=unresolved` é exatamente o estado que descreve isso
  ("temos o texto, não sabemos localizá-lo"), sem fabricar uma posição
  falsa.
- **`ReviewInfo` foi corrigido de uma forma especulativa
  (`reviewer`/`note`) para a referência append-only pedida pela auditoria:
  `review_id`, `actor_id`, `reviewed_at` (todos obrigatórios) e
  `overridden`.** A forma inicial (commit `699053ad3`) tinha inventado
  `reviewer`/`note` como campos opcionais sem base direta na spec — a spec
  só mostra `review: null` no exemplo JSON (§4.3) e menciona "ator e data"
  para overrides (§5.3), o que mapeia melhor para uma referência
  (`actor_id`/`reviewed_at`) do que para texto livre. Continua sem
  armazenamento, histórico completo, migration ou exposição em API — é só
  o tipo de referência, fora de escopo do T01.
- Nenhum score numérico de confiança foi adicionado a nenhum modelo (spec
  §4.4/§7, regra de parada explícita). Testado diretamente
  (`test_nenhum_modelo_expoe_campo_de_confianca`).
- Nenhum novo estado factual foi criado além dos 5 da spec §4.1.
- Nenhum campo potencialmente sensível foi adicionado (sem chave, prompt
  integral, chain-of-thought, header ou payload bruto).

## Dados e migrations

- Não aplicável. Nenhuma migration, tabela, banco, API, frontend, prompt ou
  classificador foi tocado. `provenance.py` não é importado por nenhum
  módulo de runtime além do próprio `radar.domain.__init__`.

## Validação

| Comando/verificação | Resultado |
|---|---|
| `PYTHONPATH=src pytest -q tests/unit/test_provenance.py tests/unit/test_edital_extraction.py` | `65 passed` (59 em `test_provenance.py` + 6 pré-existentes em `test_edital_extraction.py`) |
| `ruff check src/radar/domain/provenance.py src/radar/domain/__init__.py tests/unit/test_provenance.py tests/unit/test_edital_extraction.py` | All checks passed |
| `git diff --check` (staged) | limpo |
| Import `radar.domain.provenance` e `radar.domain` (re-export) | ok |
| `git diff` em `edital_extraction.py` | vazio (arquivo não modificado, em ambos os commits) |

Casos de teste cobertos (commit inicial + correções): construção/round-trip
(`EvidenceRef`, `FactProvenance`), todos os valores de
`FactState`/`LocatorQuality`/`ProducerKind`; `schema_version` aceita `1` e
rejeita `0`/`2`; `source` obrigatório e rejeita vazio/espaços; `page`
inválida (`0`, negativo, `None` permitido para HTML) e `block_idx`
(negativo rejeitado, `0` aceito); ausência dos dois hashes e presença de um
hash basta; `unresolved` com `page`/`block_idx`/`section_path` fabricados
(rejeitado, isolado por campo) e com `quote` mas sem posição (aceito,
interpretação confirmada); `document_only` sem `document` (rejeitado,
`None`/vazio/espaços) e com coordenadas exatas declaradas (rejeitado,
isolado por campo) e válido sem coordenadas; `exact` sem nenhuma
coordenada (rejeitado) e válido com `page`/`block_idx`/`section_path`
isoladamente; `producer.kind=default` com `state=stated` (rejeitado,
isolado da regra de evidência com `evidence_refs` presente) e com
`state=unknown` (aceito); `stated` sem `evidence_refs` (rejeitado) e com
(aceito); `inferred`/`absent`/`conflicting` sem `evidence_refs` (aceitos —
nenhuma regra nova); produtor LLM com `model`/`prompt_version`, produtores
não-LLM (`adapter`, `deterministic`, `human`, `backfill`) sem eles;
`ReviewInfo` com campos obrigatórios ausentes (rejeitado por campo),
`review_id`/`actor_id` vazios (rejeitado) e válido com `overridden=True`,
confirmando ausência de `reviewer`/`note`; forma inalterada de `Extracted`
(`{value, state, evidence}`), defaults inalterados de `EditalExtraction`,
round-trip de payload legado de `Extracted`; adaptador retornando `None`
sem evidence, convertendo substring com locator exato (`page`), com
`section_path` (produz `exact`), com só `document` (produz `document_only`),
caindo em `unresolved` sem locator (preserva `quote`), e rejeitando
ausência de hash; rejeição de campos extras em
`EvidenceRef`/`FactProvenance`/`ProducerInfo`/`ReviewInfo`; ausência de
qualquer campo com "confidence"/"score" no nome em todos os modelos do
módulo.

## Pendências

- Itens explicitamente não iniciados (fora de escopo do T01, por instrução):
  - `RT01-T02` (projeção de equivalência e fixtures) e todas as tasks
    seguintes (`T03`–`T13`) — nenhuma iniciada.
  - Nenhum produtor (adapter FINEP/FAPESP/FAPESC, tagger, constraints
    producer) foi conectado a `evidence_ref_from_extracted` ou a
    `FactProvenance` — o adaptador existe, mas não é chamado por nenhum
    caminho produtivo.
  - Nenhuma migration ou coluna `provenance jsonb` foi criada em
    `entities`/`entity_relationships` (isso é `RT01-T04`).
  - Resolução `quote → Documento Canônico/silver` (`RT01-T03`) não
    implementada; o adaptador aceita hash/locator apenas como parâmetros
    explícitos do chamador.
  - `ReviewInfo`, `DerivationInfo` e `ValidationResult` não têm nenhum
    produtor ou validador real os populando ainda — são apenas o contrato
    de forma.

## Auditoria Codex

**Veredito:** pendente — não solicitada nesta execução.
