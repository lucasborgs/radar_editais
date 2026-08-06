"""core/kg/provenance_writer.py — RT01-T05: dual-write de proveniência (FINEP).

Builders PUROS (sem I/O, sem banco, sem chamada de rede, sem cache) da
tabela de fatos aprovada para o vertical slice FINEP
(docs/execution/radar-data-trust/plans/01-provenance/RT01-T05-finep-vertical-slice.md,
spec docs/specs/radar-data-trust-01-provenance.md §4/§6). Cada função recebe
os valores já calculados pelo ingest real (status/mecanismo/tags/constraints/
requisitos/blocks/silver_hash/model/...) por parâmetro e devolve um
`FactProvenance` — ou, na função de composição `build_edital_fact_provenance`,
o dict `path -> FactProvenance.model_dump(mode="json")` pronto para
`entities.provenance`.

Chamado por `gold._ingest_editais` para todas as fontes de edital (finep na
RT01-T05; fapesp/fapesc/web desde a RT01-T06) — a decisão de quem chama fica
em `gold`, não aqui. Nenhum consumidor lê o resultado ainda
(spec §9.2 passo 1: "produtores começam a gravar... sem exigir cobertura
total do legado").

Tabela de fatos (contrato aprovado da task, ver o plano acima):

| Path                    | state                                   | producer.kind |
|-------------------------|------------------------------------------|---------------|
| `status`                | inferred                                  | deterministic |
| `mecanismo`              | inferred (só quando presente)             | deterministic |
| `setores`/`tecnologias_tags` | inferred                              | llm           |
| `constraints`            | inferred (só quando não vazio)            | llm           |
| `requisitos_texto.<i>`   | stated (locator exact/document_only) senão inferred | llm |
| aresta `operado_por`     | inferred                                  | deterministic |
| aresta `subordinado_a`   | inferred (só quando criada)               | deterministic |

`deadline` e `name` NÃO recebem proveniência aqui — dívida deliberada da
task: a âncora versionada do registro do portal (fonte/coleta/hash do
bronze) pertence à spec 04 (source-bundles), fora do escopo desta vertical
slice. Ver relatório da task, seção "Pendências".
"""
from __future__ import annotations

import hashlib
import json

from radar.core.kg.evidence_resolver import resolve_quote
from radar.domain.provenance import (
    DerivationInfo,
    EvidenceRef,
    FactProvenance,
    FactState,
    LocatorQuality,
    ProducerInfo,
    ProducerKind,
)

# ===========================================================================
# Identidade de produtor — documentada aqui porque os módulos originais
# (gold._TAGGER_SYSTEM / constraints_producer._SYSTEM_V3) são INTOCÁVEIS
# nesta task e não versionam a si próprios. Fixamos o valor CORRENTE da
# versão do prompt (não o texto do prompt) sem alterar nenhum dos dois.
# ===========================================================================

GOLD_TAGGER_PRODUCER_NAME = "gold_tagger"
GOLD_TAGGER_PROMPT_VERSION = "gold_tagger:v1"

CONSTRAINTS_PRODUCER_NAME = "constraints_producer"
#: `produce_from_text` é o entry point "v3" documentado no próprio docstring
#: do módulo (spec docs/specs/v3-unified.md) — reaproveitado aqui como
#: `producer.version`, não inventado.
CONSTRAINTS_PRODUCER_VERSION = "v3"
CONSTRAINTS_PROMPT_VERSION = "constraints_producer:v3"


# ===========================================================================
# Builders — um por linha da tabela de fatos
# ===========================================================================


def build_status_provenance(status: str | None) -> FactProvenance:  # noqa: ARG001 — valor não afeta a proveniência, só o produtor/regra
    """`status` — sempre `inferred/deterministic` (regra `gold._normalize_status`).

    Sem `EvidenceRef`: é uma normalização de dois campos crus do bronze
    (`bronze.status`, `deadline`), não uma citação documental — a tabela de
    fatos marca esta linha "sem refs"."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="_normalize_status"),
        derivation=DerivationInfo(rule="_normalize_status:v1", inputs=["bronze.status", "deadline"]),
    )


def build_mecanismo_provenance() -> FactProvenance:
    """`mecanismo` — `inferred/deterministic` (regra `gold._infer_mecanismo_from_text`).

    O chamador só inclui esta entrada no dict de saída quando
    `mecanismo is not None` (tabela de fatos: "quando presente") — esta
    função não decide isso, só constrói o valor."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="_infer_mecanismo_from_text"),
        derivation=DerivationInfo(
            rule="_infer_mecanismo_from_text:v1",
            inputs=["bronze.descricao_bronze", "silver.thematic_sections"],
        ),
    )


def build_tags_provenance(*, model: str) -> FactProvenance:
    """`setores`/`tecnologias_tags` — mesma chamada LLM (`gold._tag_edital`,
    call A), `inferred/llm`. Sem `EvidenceRef`: a tabela de fatos marca esta
    linha "sem refs" (classificação temática, não citação verbatim)."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(
            kind=ProducerKind.LLM,
            name=GOLD_TAGGER_PRODUCER_NAME,
            model=model,
            prompt_version=GOLD_TAGGER_PROMPT_VERSION,
        ),
        derivation=DerivationInfo(inputs=["silver.thematic_sections"]),
    )


def build_constraints_provenance(*, model: str) -> FactProvenance:
    """`constraints` — `inferred/llm` (`constraints_producer.produce_from_text`,
    call B). Sem `EvidenceRef` (estrutura derivada, não citação verbatim); o
    chamador só inclui esta entrada quando `constraints` não é vazio (tabela
    de fatos: "quando não vazio")."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(
            kind=ProducerKind.LLM,
            name=CONSTRAINTS_PRODUCER_NAME,
            version=CONSTRAINTS_PRODUCER_VERSION,
            model=model,
            prompt_version=CONSTRAINTS_PROMPT_VERSION,
        ),
        derivation=DerivationInfo(inputs=["silver.eligibility_sections"]),
    )


def build_requisito_provenance(
    requisito: str,
    *,
    blocks: list[dict],
    source: str,
    native_id: str | None,
    edital_id: str | None,
    silver_source_hash: str | None,
    model: str,
    source_url: str | None = None,
) -> FactProvenance:
    """Um item de `requisitos_texto` (mesma chamada B de `constraints_producer`).

    `stated` **somente** quando `evidence_resolver.resolve_quote` localiza o
    trecho no silver real com locator `exact` ou `document_only`; caso
    contrário `inferred` — nunca `stated` sem `EvidenceRef` resolvido
    (invariante do T01, reforçado aqui por construção: só promovemos a
    `stated` quando o resolver devolveu uma referência com locator
    resolvido; o `FactProvenance` em si já rejeitaria `stated` sem
    `evidence_refs`, mas a condição de `locator_quality` é mais estrita que
    isso — `unresolved` também carrega um `EvidenceRef`, mas não vira
    `stated`)."""
    result = resolve_quote(
        requisito,
        blocks,
        source=source,
        native_id=native_id,
        edital_id=edital_id,
        silver_source_hash=silver_source_hash,
        source_url=source_url,
    )
    refs: list[EvidenceRef] = [result.evidence_ref] if result.evidence_ref is not None else []
    stated = result.evidence_ref is not None and result.evidence_ref.locator_quality in (
        LocatorQuality.EXACT,
        LocatorQuality.DOCUMENT_ONLY,
    )
    return FactProvenance(
        state=FactState.STATED if stated else FactState.INFERRED,
        evidence_refs=refs,
        producer=ProducerInfo(
            kind=ProducerKind.LLM,
            name=CONSTRAINTS_PRODUCER_NAME,
            version=CONSTRAINTS_PRODUCER_VERSION,
            model=model,
            prompt_version=CONSTRAINTS_PROMPT_VERSION,
        ),
    )


def build_operado_por_provenance() -> FactProvenance:
    """Aresta `operado_por` — `inferred/deterministic` (regra `gold._SOURCE_AGENCY`).

    Só descreve o VALOR; a decisão de gravar "só na criação" (semântica de
    `on conflict do nothing`) é responsabilidade de `gold._upsert_rel`, não
    desta função."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="_SOURCE_AGENCY"),
        derivation=DerivationInfo(rule="_SOURCE_AGENCY:v1", inputs=["source"]),
    )


def build_subordinado_a_provenance() -> FactProvenance:
    """Aresta `subordinado_a` (quando criada) — `inferred/deterministic`
    (regra `gold._detect_programa`, input `name` — tabela de fatos)."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="_detect_programa"),
        derivation=DerivationInfo(rule="_detect_programa:v1", inputs=["name"]),
    )


# ===========================================================================
# Composição — dict path -> FactProvenance.model_dump(mode="json") de UM edital
# ===========================================================================


def build_edital_fact_provenance(
    *,
    status: str | None,
    mecanismo: str | None,
    constraints: list[dict],
    requisitos_texto: list[str],
    blocks: list[dict],
    source: str,
    native_id: str | None,
    edital_id: str | None = None,
    silver_source_hash: str | None = None,
    source_url: str | None = None,
    tagger_model: str,
    constraints_model: str,
) -> dict[str, dict]:
    """Compõe o dict `path -> FactProvenance.model_dump(mode="json")` completo
    de um edital, pronto para a coluna `entities.provenance`.

    Não decide SE deve rodar — o chamador (`gold._ingest_editais`) só invoca
    esta função quando `source == "finep"`. `status` é sempre incluído;
    `mecanismo`/`constraints` só quando presentes/não vazios; um
    `requisitos_texto.<i>` por item da lista (índice 0-based, na mesma ordem
    do array persistido)."""
    out: dict[str, dict] = {
        "status": build_status_provenance(status).model_dump(mode="json"),
        "setores": build_tags_provenance(model=tagger_model).model_dump(mode="json"),
        "tecnologias_tags": build_tags_provenance(model=tagger_model).model_dump(mode="json"),
    }
    if mecanismo is not None:
        out["mecanismo"] = build_mecanismo_provenance().model_dump(mode="json")
    if constraints:
        out["constraints"] = build_constraints_provenance(model=constraints_model).model_dump(mode="json")
    for i, req in enumerate(requisitos_texto or []):
        out[f"requisitos_texto.{i}"] = build_requisito_provenance(
            req,
            blocks=blocks,
            source=source,
            native_id=native_id,
            edital_id=edital_id,
            silver_source_hash=silver_source_hash,
            model=constraints_model,
            source_url=source_url,
        ).model_dump(mode="json")
    return out


# ===========================================================================
# Coordenadas silver de um match_chunk (spec §6.2)
# ===========================================================================


def chunk_storage_coords(chunk: dict, silver_source_hash: str | None) -> dict:
    """Coordenadas silver mínimas de um `match_chunk` finep (spec §6.2): o
    PRIMEIRO bloco constituinte do chunk — semântica de âncora "o chunk
    começa aqui" (ver docstring de `gold._pack_chunks`, que preserva
    `src_doc`/`src_page`/`src_idx` do primeiro bloco de cada chunk).

    `chunk` é um dict já empacotado por `_pack_chunks` (chaves aditivas
    `src_doc`/`src_page`/`src_idx`). Retorna as 4 chaves que
    `gold._replace_match_chunks` grava quando presentes
    (`document`/`page`/`silver_block_idx`/`source_hash`); dict vazio quando o
    chunk não tem nenhuma coordenada de bloco identificável (`src_doc` vazio
    e `src_page`/`src_idx` ausentes — não fabrica coordenada)."""
    if not chunk.get("src_doc") and chunk.get("src_page") is None and chunk.get("src_idx") is None:
        return {}
    return {
        "document": chunk.get("src_doc"),
        "page": chunk.get("src_page"),
        "silver_block_idx": chunk.get("src_idx"),
        "source_hash": silver_source_hash,
    }


# ===========================================================================
# RT01-T07 — ICTs (EMBRAPII + PNIPE, spec §3.2/§3.3/§6.4)
# ===========================================================================
#
# A evidência legítima desta origem (spec §6.4, linha EMBRAPII; linha PNIPE:
# docs/specs/ict-pnipe-capabilities.md) é o REGISTRO versionado do scraper —
# não um documento paginado. Cada ICT/laboratório ancora em UM `EvidenceRef`
# `document_only`: `document` é o nome do arquivo `embrapii_*.json`/
# `pnipe_*.json` usado pelo ingest (`gold._ingest_icts` já o conhece via
# `files[-1]`); `canonical_content_hash` é o md5 do JSON canônico do
# REGISTRO INDIVIDUAL (`json.dumps(record, sort_keys=True,
# ensure_ascii=False)`), não do arquivo inteiro — cada ICT do mesmo arquivo
# versionado tem hash próprio; `source_url` é a URL oficial da própria
# unidade (`record["url"]`); sem `quote` — não é uma citação textual, é a
# identidade do registro estruturado.
#
# Tabela de fatos (contrato aprovado da task RT01-T07):
#
# | Path                         | state    | producer.kind             |
# |-------------------------------|----------|----------------------------|
# | `name`, `metadata.url`        | stated   | adapter (embrapii_scraper) |
# | `uf` (só quando derivada)      | inferred | deterministic              |
# | `setores`/`tecnologias_tags`   | inferred | deterministic              |
# | aresta `credenciada_por`       | stated   | adapter (embrapii_scraper) |
#
# Campo sem valor no registro não recebe entrada no dict de saída — nada de
# `unknown` artificial (tabela de fatos da task). Sem chunks/RAG para ICTs
# (spec §3.4): esta seção não produz nenhuma coordenada de chunk.
# ===========================================================================

ICT_SCRAPER_PRODUCER_NAME = "embrapii_scraper"
PNIPE_INDEX_PRODUCER_NAME = "pnipe_lab_index"


def build_ict_record_anchor(
    *,
    record: dict,
    document: str,
    source_url: str | None,
    native_id: str | None = None,
    source: str = "embrapii",
    bundle=None,
    bundle_document=None,
) -> EvidenceRef:
    """Âncora `document_only` de UM registro ICT (spec §6.4, linha EMBRAPII;
    extensão PNIPE em docs/domain/sources/pnipe.md).

    O hash cobre só o JSON do `record` recebido, não o arquivo inteiro —
    cada ICT do mesmo `embrapii_*.json`/`pnipe_*.json` versionado tem hash
    próprio. Sem `quote`: identidade de um registro estruturado, não citação
    verbatim."""
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False)
    digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()
    ref = EvidenceRef(
        source=source,
        native_id=native_id,
        source_url=source_url,
        document=document,
        canonical_content_hash=f"md5:{digest}",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )
    if bundle is not None and bundle_document is not None:
        from radar.core.kg.source_bundle_projection import attach_bundle_lineage

        return attach_bundle_lineage(ref, bundle=bundle, document=bundle_document)
    return ref


def build_ict_identity_provenance(
    anchor: EvidenceRef, *, producer_name: str = ICT_SCRAPER_PRODUCER_NAME,
) -> FactProvenance:
    """`name`/`metadata.url` — `stated/adapter`: o scraper declara os dois
    diretamente do registro coletado (spec §3.2), não infere nenhum deles.
    Mesma âncora do registro para ambos — não são citações distintas, é o
    mesmo registro estruturado declarando os dois campos."""
    return FactProvenance(
        state=FactState.STATED,
        evidence_refs=[anchor],
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name=producer_name),
    )


def build_ict_uf_provenance() -> FactProvenance:
    """`uf` — `inferred/deterministic` (regra `gold._uf_from_address`). Sem
    `EvidenceRef`: extração de padrão sobre `address`, não citação verbatim.
    O chamador só inclui esta entrada quando a derivação teve êxito (tabela
    de fatos: "só quando derivada")."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="_uf_from_address"),
        derivation=DerivationInfo(rule="_uf_from_address:v1", inputs=["record.address"]),
    )


def build_ict_tags_provenance() -> FactProvenance:
    """`setores`/`tecnologias_tags` — `inferred/deterministic` (regras
    `normalize_setores`/`normalize_tags` sobre `areas_raw`; a tabela de
    fatos da task unifica as duas sob a regra `normalize_tags:v1`). Sem
    `EvidenceRef`: normalização determinística de vocabulário, não citação."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="normalize_tags"),
        derivation=DerivationInfo(rule="normalize_tags:v1", inputs=["record.areas_raw"]),
    )


def build_ict_credenciada_por_provenance(
    anchor: EvidenceRef, *, producer_name: str = ICT_SCRAPER_PRODUCER_NAME,
) -> FactProvenance:
    """Aresta `credenciada_por` — `stated/adapter`: a listagem EMBRAPII É a
    declaração oficial do vínculo (spec §3.3), não uma inferência sobre ele.
    Mesma âncora do registro do lado ICT da aresta — reusada pelo chamador,
    não recomputada, para não hashear o mesmo registro duas vezes."""
    return FactProvenance(
        state=FactState.STATED,
        evidence_refs=[anchor],
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name=producer_name),
    )


def build_ict_capacity_provenance(
    anchor: EvidenceRef, *, producer_name: str,
) -> FactProvenance:
    """Campos de CAPACIDADE do laboratório PNIPE (`metadata.institution`,
    `metadata.municipio`, `metadata.competencias`, `metadata.equipamentos`,
    `metadata.condicoes_acesso`, `metadata.verificado_em`) —
    `stated/adapter`: o índice do laboratório DECLARA esses campos (spec
    ict-pnipe-capabilities.md §3); não inferimos disponibilidade atual nem
    parceria. Mesma âncora `document_only` do registro — a data de
    verificação é o `data_extracao` do próprio registro."""
    return FactProvenance(
        state=FactState.STATED,
        evidence_refs=[anchor],
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name=producer_name),
    )


def build_ict_fact_provenance(
    *, record: dict, anchor: EvidenceRef, uf: str | None,
    source: str = "embrapii", producer_name: str = ICT_SCRAPER_PRODUCER_NAME,
) -> dict[str, dict]:
    """Compõe o dict `path -> FactProvenance.model_dump(mode="json")` de UM
    registro ICT, pronto para `entities.provenance`.

    Recebe `anchor` já construído (não o reconstrói) para que o chamador
    reuse a mesma âncora na aresta `credenciada_por` sem hashear o registro
    duas vezes. Campo sem valor no registro não recebe entrada — nada de
    `unknown` artificial (tabela de fatos da task). Para `source == "pnipe"`
    adiciona os paths de capacidade (ver `build_ict_capacity_provenance`)."""
    out: dict[str, dict] = {}
    identity = build_ict_identity_provenance(anchor, producer_name=producer_name)
    if record.get("name"):
        out["name"] = identity.model_dump(mode="json")
    if record.get("url"):
        out["metadata.url"] = identity.model_dump(mode="json")
    if uf is not None:
        out["uf"] = build_ict_uf_provenance().model_dump(mode="json")
    if record.get("areas_raw"):
        out["setores"] = build_ict_tags_provenance().model_dump(mode="json")
        out["tecnologias_tags"] = build_ict_tags_provenance().model_dump(mode="json")
    if source == "pnipe":
        capacity = build_ict_capacity_provenance(anchor, producer_name=producer_name)
        for key in ("institution", "municipio", "competencias", "equipamentos", "condicoes_acesso"):
            if record.get(key):
                out[f"metadata.{key}"] = capacity.model_dump(mode="json")
        if record.get("data_extracao"):
            out["metadata.verificado_em"] = capacity.model_dump(mode="json")
    return out


# ===========================================================================
# RT01-T08 — investidores, programas e agências (curadoria)
#
# docs/execution/radar-data-trust/plans/01-provenance/RT01-T08-curated-actors.md
# + spec docs/specs/radar-data-trust-01-provenance.md §3.2/§4/§6.4 ("Investidores
# e programas existentes"). Princípio: curado != validado — campos copiados
# verbatim do catálogo JSON começam `unknown` (nunca `stated`; a curadoria
# básica não é uma citação documental resolvida), mesmo carregando uma âncora
# de evidência. Campos derivados no ingest (normalização/mapeamento) são
# `inferred/deterministic`, sem refs. `constraints` de programa reusa
# `build_constraints_provenance` (mesma call B); `requisitos_texto` de
# programa NUNCA tenta `resolve_quote` — programas não têm blocos silver, a
# tabela de fatos da task proíbe inventar resolução.
#
# Builders puros (sem I/O de rede/banco); chamados por `gold._ingest_investidores`,
# `gold._ingest_programas` e `gold._get_agency`.
# ===========================================================================

#: Âncora de catálogo — documento lógico por kind (spec §6.4: "path lógico,
#: chave, ... hash/commit"). Não é um caminho de disco resolvido pelo
#: resolver do T03; é a identidade do catálogo versionado.
CURATED_INVESTIDORES_DOCUMENT = "data/silver/investidores.json"
CURATED_PROGRAMAS_DOCUMENT = "data/silver/programas.json"


def _canonical_record_hash(record: dict) -> str:
    """`md5:<hex>` do JSON canônico do registro (`sort_keys=True,
    ensure_ascii=False`) — identifica a versão do registro curado sem copiar
    o catálogo inteiro para a proveniência (spec §4.2: "ao menos um hash
    identifica a versão recuperável")."""
    canon = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return "md5:" + hashlib.md5(canon.encode("utf-8")).hexdigest()


def build_curated_catalog_anchor(
    record: dict,
    *,
    document: str,
    source_url: str | None = None,
    bundle=None,
    bundle_document=None,
) -> EvidenceRef:
    """Âncora do catálogo — UM `EvidenceRef` por entidade (investidor ou
    programa), `locator_quality=document_only` (registro identificado, sem
    coordenada exata dentro do JSON), sem `quote` (não é uma citação
    verbatim, é a referência ao registro curado inteiro). `source_url` é o
    site do ator quando declarado no catálogo; `None` quando ausente."""
    ref = EvidenceRef(
        source="curadoria",
        source_url=source_url or None,
        document=document,
        canonical_content_hash=_canonical_record_hash(record),
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )
    if bundle is not None and bundle_document is not None:
        from radar.core.kg.source_bundle_projection import attach_bundle_lineage

        return attach_bundle_lineage(ref, bundle=bundle, document=bundle_document)
    return ref


def build_catalog_copied_provenance(anchor: EvidenceRef) -> FactProvenance:
    """Campo copiado verbatim do catálogo (`name`/`description`/`metadata.*`)
    — SEMPRE `unknown/human` (spec §3.2: "campos de curadoria básica... começam
    como unknown"; curado != validado). Carrega a âncora do catálogo como
    evidência de origem, sem prometer que o valor foi verificado."""
    return FactProvenance(
        state=FactState.UNKNOWN,
        evidence_refs=[anchor],
        producer=ProducerInfo(kind=ProducerKind.HUMAN, name="curadoria"),
    )


def build_curated_derived_provenance(*, producer_name: str, rule: str, inputs: list[str]) -> FactProvenance:
    """Campo derivado no ingest (normalização/mapeamento determinístico sobre
    o registro curado) — `inferred/deterministic`, sem `EvidenceRef` (regra
    sobre um campo de entrada, não citação verbatim). Uma função por linha
    da tabela de fatos da task chama isto com o `rule`/`inputs` próprios."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name=producer_name),
        derivation=DerivationInfo(rule=rule, inputs=inputs),
    )


def build_programa_requisito_provenance(*, model: str) -> FactProvenance:
    """`requisitos_texto.<i>` de PROGRAMA — `inferred/llm`, mesma call B
    (`constraints_producer.produce_from_text`) do edital, mas SEM
    `evidence_resolver.resolve_quote`: programas não têm blocos silver
    (tabela de fatos T08: "não inventar resolução"). Nunca `stated`."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(
            kind=ProducerKind.LLM,
            name=CONSTRAINTS_PRODUCER_NAME,
            version=CONSTRAINTS_PRODUCER_VERSION,
            model=model,
            prompt_version=CONSTRAINTS_PROMPT_VERSION,
        ),
        derivation=DerivationInfo(inputs=["record.elegibilidade"]),
    )


def build_programa_operado_por_provenance() -> FactProvenance:
    """Aresta `operado_por` de PROGRAMA — `inferred/deterministic`, regra
    `gold._split_operador` (distinta da versão de edital, que usa
    `_SOURCE_AGENCY`)."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="_split_operador"),
        derivation=DerivationInfo(rule="_split_operador:v1", inputs=["record.operador"]),
    )


def build_agencia_name_provenance() -> FactProvenance:
    """`name` de uma entidade `agencia` (`gold._get_agency`) —
    `inferred/deterministic`, regra `gold._canon_agency`. `inputs` é o valor
    literal da tabela de fatos da task (`"operador|source"`): a canonicalização
    roda tanto sobre um token de `operador` de programa quanto sobre a fonte
    de um edital — o chamador não distingue qual das duas origens disparou
    esta chamada, então o input declarado cobre ambas."""
    return FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="_canon_agency"),
        derivation=DerivationInfo(rule="_canon_agency:v1", inputs=["operador|source"]),
    )


def build_investidor_fact_provenance(
    record: dict,
    *,
    setores: list[str],
    tecnologias_tags: list[str],
    status: str | None,
    ticket_min: float | None,
    ticket_max: float | None,
    bundle=None,
    bundle_document=None,
) -> dict[str, dict]:
    """Compõe o dict `path -> FactProvenance.model_dump(mode="json")` de UM
    investidor, pronto para `entities.provenance`. `record` é o registro cru
    de `investidores.json` — usado tanto para a âncora quanto para decidir
    quais paths copiados têm valor ("campo sem valor -> sem entrada").
    `setores`/`tecnologias_tags`/`status`/`ticket_min`/`ticket_max` são os
    valores JÁ computados pelo chamador (`gold._ingest_investidores`); esta
    função não recalcula nada, só anota a proveniência."""
    anchor = build_curated_catalog_anchor(
        record,
        document=CURATED_INVESTIDORES_DOCUMENT,
        source_url=record.get("site") or None,
        bundle=bundle,
        bundle_document=bundle_document,
    )
    copied = build_catalog_copied_provenance(anchor).model_dump(mode="json")
    out: dict[str, dict] = {}
    if record.get("name"):
        out["name"] = copied
    if record.get("tese"):
        out["description"] = copied
    if record.get("site"):
        out["metadata.site"] = copied
    out["setores"] = build_curated_derived_provenance(
        producer_name="normalize_setores", rule="normalize_setores:v1",
        inputs=["record.setores", "record.tese_themes"],
    ).model_dump(mode="json")
    out["tecnologias_tags"] = build_curated_derived_provenance(
        producer_name="normalize_tags", rule="normalize_tags:v1",
        inputs=["record.tese_keywords"],
    ).model_dump(mode="json")
    if status is not None:
        out["status"] = build_curated_derived_provenance(
            producer_name="_status_from_curated", rule="_status_from_curated:v1",
            inputs=["record.fund_status"],
        ).model_dump(mode="json")
    if ticket_min is not None:
        out["ticket_min"] = build_curated_derived_provenance(
            producer_name="_ticket_from_range", rule="_ticket_from_range:v1",
            inputs=["record.ticket_range"],
        ).model_dump(mode="json")
    if ticket_max is not None:
        out["ticket_max"] = build_curated_derived_provenance(
            producer_name="_ticket_from_range", rule="_ticket_from_range:v1",
            inputs=["record.ticket_range"],
        ).model_dump(mode="json")
    return out


def build_programa_fact_provenance(
    record: dict,
    *,
    setores: list[str],
    tecnologias_tags: list[str],
    status: str | None,
    ticket_min: float | None,
    ticket_max: float | None,
    mecanismo: str | None,
    formato: str | None,
    constraints: list[dict],
    requisitos_texto: list[str],
    constraints_model: str,
    bundle=None,
    bundle_document=None,
) -> dict[str, dict]:
    """Compõe o dict `path -> FactProvenance.model_dump(mode="json")` de UM
    programa, pronto para `entities.provenance`. Mesma convenção de
    `build_investidor_fact_provenance`: `record` é o registro cru de
    `programas.json`; os demais parâmetros são valores JÁ computados pelo
    chamador (`gold._ingest_programas`)."""
    anchor = build_curated_catalog_anchor(
        record,
        document=CURATED_PROGRAMAS_DOCUMENT,
        source_url=record.get("site") or None,
        bundle=bundle,
        bundle_document=bundle_document,
    )
    copied = build_catalog_copied_provenance(anchor).model_dump(mode="json")
    out: dict[str, dict] = {}
    if record.get("name"):
        out["name"] = copied
    if record.get("operador"):
        out["metadata.operador"] = copied
    if record.get("beneficio"):
        out["metadata.beneficio"] = copied
    if record.get("elegibilidade"):
        out["metadata.elegibilidade"] = copied
    out["setores"] = build_curated_derived_provenance(
        producer_name="normalize_setores", rule="normalize_setores:v1",
        inputs=["record.setores", "record.tese_themes"],
    ).model_dump(mode="json")
    out["tecnologias_tags"] = build_curated_derived_provenance(
        producer_name="normalize_tags", rule="normalize_tags:v1",
        inputs=["record.tese_themes"],
    ).model_dump(mode="json")
    if status is not None:
        out["status"] = build_curated_derived_provenance(
            producer_name="_status_from_curated", rule="_status_from_curated:v1",
            inputs=["record.status"],
        ).model_dump(mode="json")
    if ticket_min is not None:
        out["ticket_min"] = build_curated_derived_provenance(
            producer_name="_ticket_from_range", rule="_ticket_from_range:v1",
            inputs=["record.ticket_range"],
        ).model_dump(mode="json")
    if ticket_max is not None:
        out["ticket_max"] = build_curated_derived_provenance(
            producer_name="_ticket_from_range", rule="_ticket_from_range:v1",
            inputs=["record.ticket_range"],
        ).model_dump(mode="json")
    if mecanismo is not None:
        out["mecanismo"] = build_curated_derived_provenance(
            producer_name="_map_mecanismo", rule="_map_mecanismo:v1",
            inputs=["record.tipo"],
        ).model_dump(mode="json")
    if formato is not None:
        out["formato"] = build_curated_derived_provenance(
            producer_name="_map_formato", rule="_map_formato:v1",
            inputs=["record.formato"],
        ).model_dump(mode="json")
    if constraints:
        out["constraints"] = build_constraints_provenance(model=constraints_model).model_dump(mode="json")
    for i, _req in enumerate(requisitos_texto or []):
        out[f"requisitos_texto.{i}"] = build_programa_requisito_provenance(
            model=constraints_model
        ).model_dump(mode="json")
    return out


__all__ = [
    "GOLD_TAGGER_PRODUCER_NAME",
    "GOLD_TAGGER_PROMPT_VERSION",
    "CONSTRAINTS_PRODUCER_NAME",
    "CONSTRAINTS_PRODUCER_VERSION",
    "CONSTRAINTS_PROMPT_VERSION",
    "build_status_provenance",
    "build_mecanismo_provenance",
    "build_tags_provenance",
    "build_constraints_provenance",
    "build_requisito_provenance",
    "build_operado_por_provenance",
    "build_subordinado_a_provenance",
    "build_edital_fact_provenance",
    "chunk_storage_coords",
    "ICT_SCRAPER_PRODUCER_NAME",
    "build_ict_record_anchor",
    "build_ict_identity_provenance",
    "build_ict_uf_provenance",
    "build_ict_tags_provenance",
    "build_ict_credenciada_por_provenance",
    "build_ict_fact_provenance",
    "CURATED_INVESTIDORES_DOCUMENT",
    "CURATED_PROGRAMAS_DOCUMENT",
    "build_curated_catalog_anchor",
    "build_catalog_copied_provenance",
    "build_curated_derived_provenance",
    "build_programa_requisito_provenance",
    "build_programa_operado_por_provenance",
    "build_agencia_name_provenance",
    "build_investidor_fact_provenance",
    "build_programa_fact_provenance",
]
