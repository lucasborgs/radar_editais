"""Leitura do subconjunto público de proveniência (RT01-T10).

UMA função pura: `public_provenance(stored) -> dict` — traduz o JSONB
`entities.provenance` para o envelope público descrito na spec
radar-data-trust-01-provenance.md §6.3, sem expor campos operacionais.

Contrato:
  - stored None / {} / malformado → {} (nunca levanta, nunca inventa estado).
  - path AUSENTE no dict público = unknown/legado — o consumidor não pode
    tratar ausência como certeza.
  - citations inclui SOMENTE EvidenceRefs com locator_quality exact ou
    document_only (unresolved tem quote mas não localização verificável —
    não vira citação pública).
  - Campos operacionais (producer, derivation, validations, hashes, review)
    NÃO são expostos.
"""

from __future__ import annotations

from typing import Any

_CITATION_KEYS = frozenset({"document", "page", "quote", "source_url", "collected_at"})


def public_provenance(stored: dict | None) -> dict:
    """Retorna o subconjunto público de *stored* (entities.provenance).

    Cada path mapeia para ``{"state": <str>, "citations": [...]}`` onde
    citations contém somente as evidências com localização verificável
    (locator_quality = exact ou document_only).
    """
    if not stored or not isinstance(stored, dict):
        return {}

    out: dict[str, Any] = {}
    for path, fp in stored.items():
        if not isinstance(fp, dict):
            continue
        state = fp.get("state")
        if not isinstance(state, str) or not state:
            continue
        refs = fp.get("evidence_refs")
        if not isinstance(refs, list):
            out[path] = {"state": state, "citations": []}
            continue
        citations = [_public_citation(r) for r in refs if _is_public_citable(r)]
        out[path] = {"state": state, "citations": citations}
    return out


def _is_public_citable(ref: Any) -> bool:
    if not isinstance(ref, dict):
        return False
    lq = ref.get("locator_quality")
    return lq in ("exact", "document_only")


def _public_citation(ref: dict) -> dict:
    return {k: ref.get(k) for k in _CITATION_KEYS}
