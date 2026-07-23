# RT01-T01 — Tipos de proveniência

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T01-provenance-types.md`](../../plans/01-provenance/RT01-T01-provenance-types.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t01` / `e78989876b159418c58a44806094f1e635692db1`
**Commits de implementação:** `699053ad3` — contrato de proveniência
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
- `src/radar/domain/__init__.py` atualizado: exporta os 10 símbolos novos
  (`FactState`, `LocatorQuality`, `ProducerKind`, `EvidenceRef`,
  `ProducerInfo`, `DerivationInfo`, `ValidationResult`, `ReviewInfo`,
  `FactProvenance`, `evidence_ref_from_extracted`, `PROVENANCE_SCHEMA_VERSION`),
  sem alterar nenhum export existente.
- `src/radar/domain/edital_extraction.py` **não foi alterado** — ver
  "Divergências e decisões" sobre a escolha de compatibilidade.
- Criados 34 testes em `tests/unit/test_provenance.py`.

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
- **`quote` não é tratado como "coordenada" sob `locator_quality=unresolved`.**
  A instrução final e a spec (§4.2) dizem "unresolved registra falha sem
  fabricar coordenadas exatas". Interpretado como: coordenadas = posição no
  documento (`page`, `block_idx`, `section_path`); `quote` é conteúdo
  (o texto capturado), não uma alegação de posição. Essa leitura é
  necessária para o adaptador funcionar: um `Extracted.evidence` sem
  documento/página conhecidos ainda carrega o texto verbatim legado, e
  `locator_quality=unresolved` é exatamente o estado que descreve isso
  ("temos o texto, não sabemos localizá-lo"). Se `quote` fosse proibido sob
  `unresolved`, o adaptador não teria como representar esse caso comum sem
  fabricar uma posição falsa — o oposto do que a spec pede. Registrando a
  divergência de interpretação para revisão do proprietário caso a leitura
  pretendida fosse mais restritiva.
- **`ReviewInfo` foi modelado com forma mínima não detalhada na spec.** A
  spec só mostra `review: null` no exemplo JSON (§4.3) e menciona "ator e
  data" para overrides (§5.3). Implementado com `reviewer: str | None`,
  `note: str | None`, `reviewed_at: datetime | None`, `overridden: bool`,
  sem inventar um enum de decisão de revisão (isso seria criar estrutura
  normativa não pedida). Não há campo livre-form ou histórico completo —
  fora de escopo do T01.
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
| `PYTHONPATH=src pytest tests/unit/test_provenance.py tests/unit/test_edital_extraction.py` | `40 passed` (34 novos + 6 pré-existentes) |
| `ruff check src/radar/domain/provenance.py src/radar/domain/__init__.py src/radar/domain/edital_extraction.py tests/unit/test_provenance.py tests/unit/test_edital_extraction.py` | All checks passed |
| `git diff --check` (staged) | limpo |
| Import `radar.domain.provenance` e `radar.domain` (re-export) | ok |
| `git diff` em `edital_extraction.py` | vazio (arquivo não modificado) |

Casos de teste cobertos: construção/round-trip (`EvidenceRef`,
`FactProvenance`), todos os valores de `FactState`/`LocatorQuality`/
`ProducerKind`, `page` inválida (0, negativo, `None` permitido para HTML),
ausência dos dois hashes, presença de um hash basta, `unresolved` com
`page`/`block_idx`/`section_path` fabricados (rejeitado, isolado por campo),
`unresolved` com `quote` mas sem posição (aceito), `producer.kind=default`
com `state=stated` (rejeitado) e com `state=unknown` (aceito), produtor LLM
com `model`/`prompt_version`, produtores não-LLM (`adapter`,
`deterministic`, `human`, `backfill`) sem eles, forma inalterada de
`Extracted` (`{value, state, evidence}`), defaults inalterados de
`EditalExtraction`, round-trip de payload legado de `Extracted`, adaptador
retornando `None` sem evidence, convertendo substring com locator exato,
caindo em `unresolved` sem locator, e rejeitando ausência de hash; rejeição
de campos extras em `EvidenceRef`/`FactProvenance`/`ProducerInfo`/
`ReviewInfo`; ausência de qualquer campo com "confidence"/"score" no nome
em todos os modelos do módulo.

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
