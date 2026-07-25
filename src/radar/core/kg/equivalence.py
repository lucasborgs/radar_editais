"""core/kg/equivalence.py — projeção canônica do gold + comparador por chaves
naturais (RT01-T02, spec docs/specs/radar-data-trust-01-provenance.md §9.3).

Congela a forma FUNCIONAL da ingestão gold (`radar.core.kg.gold.ingest_all`)
antes de qualquer dual-write de proveniência: entidades por chave natural
`(kind, source, native_id)`, relações por `(chave_origem, chave_destino, type)`
e `match_chunks` por entidade. `diff_projections` compara duas capturas e
reporta toda divergência exceto os campos explicitamente listados em
`IGNORED_*` — a lista de ignore que a spec pede "explícita e versionada"
(`IGNORE_LIST_VERSION`).

Este módulo é PURO por desenho: sem I/O de rede ou banco, sem import de
`tests/`. Quem produz uma `GoldProjection` real (executando o ingest com
stubs nos seams de infraestrutura) é o harness em
`tests/helpers/gold_projection.py` — este módulo só define o contrato de
forma e o comparador.

Não é golden de avaliação (`data/evaluation/`): é fixture/contrato de teste
para provar equivalência estrutural old-path × dual-write nas tasks
RT01-T05/T06/T12. Nenhum score de confiança, nenhum estado factual novo.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

PROJECTION_SCHEMA_VERSION: int = 1

# ===========================================================================
# Lista de ignore (spec §9.3) — explícita e versionada. `diff_projections`
# nunca acusa divergência nestes campos, mesmo que presentes em um lado e
# ausentes no outro (ex.: `provenance` só existe na projeção dual-write).
# Bump `IGNORE_LIST_VERSION` sempre que este conjunto mudar.
# ===========================================================================

IGNORE_LIST_VERSION: int = 1

#: IDs físicos gerados pelo upsert e timestamps operacionais + a coluna
#: aditiva `provenance` (RT01-T04+) — nunca existiu no old-path.
IGNORED_ENTITY_FIELDS: frozenset[str] = frozenset({
    "id", "created_at", "updated_at", "provenance",
})
IGNORED_RELATION_FIELDS: frozenset[str] = frozenset({
    "id", "source_id", "target_id", "created_at", "updated_at", "provenance",
})
IGNORED_CHUNK_FIELDS: frozenset[str] = frozenset({
    "id", "entity_id", "created_at", "updated_at", "provenance",
})


def sha256_of(text: str | None) -> str | None:
    """sha256 hex determinístico de um texto de input (nunca do vetor de
    embedding). `None`/vazio → `None` (nada a hashear, não um hash de string
    vazia — evita colisão silenciosa entre "sem input" e "input vazio")."""
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ===========================================================================
# Chaves naturais
# ===========================================================================

def entity_key(kind: str, source: str, native_id: str) -> str:
    """Chave natural de uma entidade: `(kind, source, native_id)` — spec §9.3."""
    return f"{kind}|{source}|{native_id}"


def relation_key(source_entity_key: str, target_entity_key: str, rel_type: str) -> str:
    """Chave natural de uma relação: `(chave_origem, chave_destino, type)`."""
    return f"{source_entity_key}->{target_entity_key}|{rel_type}"


# ===========================================================================
# Construtores de registro (dicts JSON-safe; nenhum tipo Python específico
# como `date`/`Decimal` — o chamador normaliza para ISO/número antes de
# construir o registro)
# ===========================================================================

def make_entity_record(
    *,
    kind: str,
    source: str,
    native_id: str,
    name: str | None = None,
    description: str | None = None,
    mecanismo: str | None = None,
    formato: str | None = None,
    setores: list[str] | None = None,
    tecnologias_tags: list[str] | None = None,
    status: str | None = None,
    deadline: str | None = None,
    uf: str | None = None,
    ticket_min: float | None = None,
    ticket_max: float | None = None,
    constraints: list[dict] | None = None,
    requisitos_texto: list[str] | None = None,
    curated: bool = False,
    verificado_em: str | None = None,
    metadata: dict | None = None,
    embedding_input_hash: str | None = None,
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
    tagger_input_hash: str | None = None,
    constraints_input_hash: str | None = None,
) -> dict:
    """Registro canônico de uma entidade (spec §9.3): campos escalares, arrays,
    constraints, requisitos_texto, metadata, status, deadline (ISO), curated,
    uf, e o hash/modelo/dimensionalidade do INPUT de embedding — nunca o vetor.

    `tagger_input_hash`/`constraints_input_hash` são campos aditivos que o
    harness de captura preenche quando a entidade passou por um produtor LLM
    stubado (tagger de editais / constraints_producer de editais e programas);
    `None` quando não aplicável a esse `kind`.
    """
    return {
        "kind": kind,
        "source": source,
        "native_id": native_id,
        "name": name,
        "description": description,
        "mecanismo": mecanismo,
        "formato": formato,
        "setores": list(setores or []),
        "tecnologias_tags": list(tecnologias_tags or []),
        "status": status,
        "deadline": deadline,
        "uf": uf,
        "ticket_min": ticket_min,
        "ticket_max": ticket_max,
        "constraints": list(constraints or []),
        "requisitos_texto": list(requisitos_texto or []),
        "curated": bool(curated),
        "verificado_em": verificado_em,
        "metadata": dict(metadata or {}),
        "embedding_input_hash": embedding_input_hash,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "tagger_input_hash": tagger_input_hash,
        "constraints_input_hash": constraints_input_hash,
    }


def make_relation_record(
    *,
    source_entity_key: str,
    target_entity_key: str,
    type: str,  # noqa: A002 — nome do campo espelha a coluna `entity_relationships.type`
    properties: dict | None = None,
) -> dict:
    """Registro canônico de uma relação (spec §9.3): `(chave_origem,
    chave_destino, type)` + `properties`."""
    return {
        "source_entity_key": source_entity_key,
        "target_entity_key": target_entity_key,
        "type": type,
        "properties": dict(properties or {}),
    }


def make_chunk_record(
    *,
    idx: int,
    section_path: list[str] | None = None,
    kind: str | None = None,
    text: str,
    embedding_input_hash: str | None = None,
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
) -> dict:
    """Registro canônico de um `match_chunk`: idx, text e coordenadas
    (section_path/kind), mais o hash/modelo/dimensionalidade do input de
    embedding do chunk (nunca o vetor)."""
    return {
        "idx": idx,
        "section_path": list(section_path or []),
        "kind": kind,
        "text": text,
        "embedding_input_hash": embedding_input_hash,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
    }


# ===========================================================================
# GoldProjection — estrutura mutável de acumulação + serialização
# ===========================================================================

@dataclass
class GoldProjection:
    """Projeção canônica acumulada de uma execução de `gold.ingest_all`.

    `entities`/`relations` são indexados pela chave natural (string
    determinística via `entity_key`/`relation_key`); `match_chunks` é indexado
    pela chave natural da entidade dona dos chunks.
    """

    entities: dict[str, dict] = field(default_factory=dict)
    relations: dict[str, dict] = field(default_factory=dict)
    match_chunks: dict[str, list[dict]] = field(default_factory=dict)

    def add_entity(self, record: dict) -> str:
        key = entity_key(record["kind"], record["source"], record["native_id"])
        self.entities[key] = record
        return key

    def add_relation(self, record: dict) -> str:
        key = relation_key(record["source_entity_key"], record["target_entity_key"], record["type"])
        self.relations[key] = record
        return key

    def set_match_chunks(self, entity_key_: str, records: list[dict]) -> None:
        self.match_chunks[entity_key_] = list(records)

    def to_dict(self) -> dict:
        return {
            "schema_version": PROJECTION_SCHEMA_VERSION,
            "entities": self.entities,
            "relations": self.relations,
            "match_chunks": self.match_chunks,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GoldProjection:
        return cls(
            entities=dict(data.get("entities") or {}),
            relations=dict(data.get("relations") or {}),
            match_chunks={k: list(v) for k, v in (data.get("match_chunks") or {}).items()},
        )

    def to_json(self) -> str:
        return to_json(self.to_dict())


def to_json(projection: dict) -> str:
    """Serialização JSON determinística: chaves ordenadas, UTF-8 literal
    (`ensure_ascii=False`), indentação estável para diff textual legível."""
    return json.dumps(projection, sort_keys=True, ensure_ascii=False, indent=2)


# ===========================================================================
# Comparador
# ===========================================================================

@dataclass(frozen=True)
class Divergence:
    """Uma divergência estrutural entre duas projeções."""

    category: str
    key: str
    field: str | None = None
    old_value: Any = None
    new_value: Any = None

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "key": self.key,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }

    def __str__(self) -> str:  # pragma: no cover — só afeta legibilidade de output
        if self.field is None:
            return f"[{self.category}] {self.key}"
        return f"[{self.category}] {self.key}.{self.field}: {self.old_value!r} != {self.new_value!r}"


def _as_dict(projection: GoldProjection | dict) -> dict:
    return projection.to_dict() if isinstance(projection, GoldProjection) else projection


def _diff_records(
    category_prefix: str, old: dict[str, dict], new: dict[str, dict], ignored: frozenset[str]
) -> list[Divergence]:
    out: list[Divergence] = []
    old_keys, new_keys = set(old), set(new)
    for k in sorted(old_keys - new_keys):
        out.append(Divergence(f"{category_prefix}_missing", k))
    for k in sorted(new_keys - old_keys):
        out.append(Divergence(f"{category_prefix}_extra", k))
    for k in sorted(old_keys & new_keys):
        old_rec, new_rec = old[k], new[k]
        fields = (set(old_rec) | set(new_rec)) - ignored
        for f in sorted(fields):
            ov, nv = old_rec.get(f), new_rec.get(f)
            if ov != nv:
                out.append(Divergence(f"{category_prefix}_field", k, field=f, old_value=ov, new_value=nv))
    return out


def _diff_match_chunks(old: dict[str, list[dict]], new: dict[str, list[dict]]) -> list[Divergence]:
    out: list[Divergence] = []
    for ek in sorted(set(old) | set(new)):
        old_by_idx = {c["idx"]: c for c in old.get(ek, [])}
        new_by_idx = {c["idx"]: c for c in new.get(ek, [])}
        for idx in sorted(set(old_by_idx) - set(new_by_idx)):
            out.append(Divergence("chunk_missing", ek, field=str(idx)))
        for idx in sorted(set(new_by_idx) - set(old_by_idx)):
            out.append(Divergence("chunk_extra", ek, field=str(idx)))
        for idx in sorted(set(old_by_idx) & set(new_by_idx)):
            old_c, new_c = old_by_idx[idx], new_by_idx[idx]
            fields = (set(old_c) | set(new_c)) - IGNORED_CHUNK_FIELDS - {"idx"}
            for f in sorted(fields):
                ov, nv = old_c.get(f), new_c.get(f)
                if ov != nv:
                    out.append(Divergence("chunk_field", f"{ek}#{idx}", field=f, old_value=ov, new_value=nv))
    return out


def diff_projections(old: GoldProjection | dict, new: GoldProjection | dict) -> list[Divergence]:
    """Compara duas projeções canônicas por chave natural.

    Ignora SOMENTE (spec §9.3): IDs físicos gerados, timestamps operacionais
    e campos novos de proveniência — `IGNORED_ENTITY_FIELDS`/
    `IGNORED_RELATION_FIELDS`/`IGNORED_CHUNK_FIELDS` (versionados em
    `IGNORE_LIST_VERSION`). Qualquer outra divergência de campo escalar,
    array, constraint, metadata, entidade ausente/extra, relação
    ausente/extra ou chunk com texto/coordenada alterada é reportada.

    Retorna lista vazia quando as projeções são equivalentes.
    """
    old_d, new_d = _as_dict(old), _as_dict(new)
    out: list[Divergence] = []
    out += _diff_records("entity", old_d.get("entities", {}), new_d.get("entities", {}), IGNORED_ENTITY_FIELDS)
    out += _diff_records("relation", old_d.get("relations", {}), new_d.get("relations", {}), IGNORED_RELATION_FIELDS)
    out += _diff_match_chunks(old_d.get("match_chunks", {}), new_d.get("match_chunks", {}))
    return out


def render_divergences(divergences: list[Divergence]) -> str:
    """Relatório humano de uma lista de divergências (usado em mensagens de
    falha de teste); string vazia quando não há divergência."""
    return "\n".join(str(d) for d in divergences)


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "IGNORE_LIST_VERSION",
    "IGNORED_ENTITY_FIELDS",
    "IGNORED_RELATION_FIELDS",
    "IGNORED_CHUNK_FIELDS",
    "sha256_of",
    "entity_key",
    "relation_key",
    "make_entity_record",
    "make_relation_record",
    "make_chunk_record",
    "GoldProjection",
    "to_json",
    "Divergence",
    "diff_projections",
    "render_divergences",
]
