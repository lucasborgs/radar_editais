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
# RT01-T07 — ICTs EMBRAPII (spec §3.2/§3.3/§6.4)
# ===========================================================================
#
# A evidência legítima desta origem (spec §6.4, linha EMBRAPII) é o REGISTRO
# versionado do scraper — não um documento paginado. Cada ICT ancora em UM
# `EvidenceRef` `document_only`: `document` é o nome do arquivo
# `embrapii_*.json` usado pelo ingest (`gold._ingest_icts` já o conhece via
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


def build_ict_record_anchor(
    *, record: dict, document: str, source_url: str | None, native_id: str | None = None,
) -> EvidenceRef:
    """Âncora `document_only` de UM registro EMBRAPII (spec §6.4).

    O hash cobre só o JSON do `record` recebido, não o arquivo inteiro —
    cada ICT do mesmo `embrapii_*.json` versionado tem hash próprio. Sem
    `quote`: identidade de um registro estruturado, não citação verbatim."""
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False)
    digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()
    return EvidenceRef(
        source="embrapii",
        native_id=native_id,
        source_url=source_url,
        document=document,
        canonical_content_hash=f"md5:{digest}",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )


def build_ict_identity_provenance(anchor: EvidenceRef) -> FactProvenance:
    """`name`/`metadata.url` — `stated/adapter`: o scraper declara os dois
    diretamente do registro coletado (spec §3.2), não infere nenhum deles.
    Mesma âncora do registro para ambos — não são citações distintas, é o
    mesmo registro estruturado declarando os dois campos."""
    return FactProvenance(
        state=FactState.STATED,
        evidence_refs=[anchor],
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name=ICT_SCRAPER_PRODUCER_NAME),
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


def build_ict_credenciada_por_provenance(anchor: EvidenceRef) -> FactProvenance:
    """Aresta `credenciada_por` — `stated/adapter`: a listagem EMBRAPII É a
    declaração oficial do vínculo (spec §3.3), não uma inferência sobre ele.
    Mesma âncora do registro do lado ICT da aresta — reusada pelo chamador,
    não recomputada, para não hashear o mesmo registro duas vezes."""
    return FactProvenance(
        state=FactState.STATED,
        evidence_refs=[anchor],
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name=ICT_SCRAPER_PRODUCER_NAME),
    )


def build_ict_fact_provenance(*, record: dict, anchor: EvidenceRef, uf: str | None) -> dict[str, dict]:
    """Compõe o dict `path -> FactProvenance.model_dump(mode="json")` de UM
    registro ICT, pronto para `entities.provenance`.

    Recebe `anchor` já construído (não o reconstrói) para que o chamador
    reuse a mesma âncora na aresta `credenciada_por` sem hashear o registro
    duas vezes. Campo sem valor no registro não recebe entrada — nada de
    `unknown` artificial (tabela de fatos da task)."""
    out: dict[str, dict] = {}
    if record.get("name"):
        out["name"] = build_ict_identity_provenance(anchor).model_dump(mode="json")
    if record.get("url"):
        out["metadata.url"] = build_ict_identity_provenance(anchor).model_dump(mode="json")
    if uf is not None:
        out["uf"] = build_ict_uf_provenance().model_dump(mode="json")
    if record.get("areas_raw"):
        out["setores"] = build_ict_tags_provenance().model_dump(mode="json")
        out["tecnologias_tags"] = build_ict_tags_provenance().model_dump(mode="json")
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
]
