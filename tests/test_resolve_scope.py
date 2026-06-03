"""
Testes de `KGMatchService.resolve_scope` — trigger → list[edital_ids].

Usam o vault REAL do projeto (`obsidian_vault/radar-editais/`): a lógica é
traversal de wikilinks em .md. Para não quebrar a cada novo scrape, os asserts
são **estruturais** (formato, dedup, consistência) e descobrem um cluster real
do vault em runtime, em vez de hardcodar IDs.

Pós-multi-fonte: os nós do grafo usam slug colon-free no nome (`finep-589`),
mas `resolve_scope` devolve o id real prefixado (`finep:589`) — é o que o
retrieve_chunks/get_edital_by_id esperam.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import OBSIDIAN_VAULT_DIR  # noqa: E402
from core.edital_id import slug_to_id  # noqa: E402
from core.kg_match_service import KGMatchService  # noqa: E402

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_TEMAS_DIR = OBSIDIAN_VAULT_DIR / "radar-editais" / "temas"


def _editais_in_tema_file(path: Path) -> list[str]:
    """IDs reais (prefixados) dos editais wikilinkados num .md de tema, em ordem."""
    ids: list[str] = []
    for target in _WIKILINK_RE.findall(path.read_text(encoding="utf-8")):
        parts = target.strip().split("/")
        if len(parts) >= 2 and parts[-2] == "editais":
            ids.append(slug_to_id(parts[-1]))
    return ids


def _pick_tema(min_editais: int = 1) -> tuple[str, list[str]] | None:
    """Acha um tema do vault com >= min_editais. Retorna (node_id, [ids reais])."""
    if not _TEMAS_DIR.exists():
        return None
    best: tuple[str, list[str]] | None = None
    for p in sorted(_TEMAS_DIR.glob("*.md")):
        ids = _editais_in_tema_file(p)
        if len(ids) >= min_editais:
            node_id = f"radar-editais/temas/{p.stem}"
            # prefere o maior cluster (mais útil para o teste de análogos)
            if best is None or len(ids) > len(best[1]):
                best = (node_id, ids)
    return best


def _is_prefixed(eid: str) -> bool:
    return ":" in eid


# =============================================================================
# Tests
# =============================================================================

def test_resolve_scope_node_tema():
    """Clique num tema → exatamente os editais que ele lista (ids prefixados), sem análogos."""
    picked = _pick_tema(min_editais=1)
    assert picked is not None, "vault sem temas — rode build_kg + export_to_obsidian"
    node_id, expected = picked
    result = KGMatchService().resolve_scope(node_id=node_id, node_type="tema")
    assert result == expected
    assert all(_is_prefixed(e) for e in result), f"esperado ids prefixados, veio {result}"


def test_resolve_scope_edital_node_includes_analogues():
    """Clique num nó edital → primário + análogos via traversal reverso."""
    picked = _pick_tema(min_editais=2)
    assert picked is not None, "vault sem tema com >=2 editais"
    _, cluster = picked
    primary = cluster[0]
    result = KGMatchService().resolve_scope(
        node_id=f"radar-editais/editais/{primary.replace(':', '-')}",
        node_type="edital",
        max_analogues=3,
    )
    assert result[0] == primary
    assert len(result) <= 4
    assert all(_is_prefixed(e) for e in result)
    # ao menos um análogo deve pertencer ao mesmo cluster temático do primário
    assert any(eid in set(cluster[1:]) for eid in result[1:])


def test_resolve_scope_edital_id_session():
    """Sessão com edital_id deve produzir o mesmo resultado do clique no nó."""
    picked = _pick_tema(min_editais=2)
    assert picked is not None
    primary = picked[1][0]
    svc = KGMatchService()
    via_session = svc.resolve_scope(edital_id=primary)
    via_node = svc.resolve_scope(
        node_id=f"radar-editais/editais/{primary.replace(':', '-')}",
        node_type="edital",
    )
    assert via_session == via_node


def test_resolve_scope_edital_sem_vault_page():
    """ID inexistente no vault não deve crashar — só retorna o próprio ID."""
    result = KGMatchService().resolve_scope(edital_id="finep:9999")
    assert result == ["finep:9999"]


def test_resolve_scope_free_trigger():
    """Sem trigger algum → todos os editais do índice (strings)."""
    from core import kg_store
    if not kg_store.load_index().get("editais"):
        import pytest
        pytest.skip("requer knowledge_graph/index.json gerado (ausente em CI limpo)")
    result = KGMatchService().resolve_scope()
    assert len(result) > 0
    assert all(isinstance(eid, str) for eid in result)


def test_resolve_scope_max_analogues_cap():
    """Maior cluster ainda respeita o cap em max_analogues."""
    picked = _pick_tema(min_editais=2)
    assert picked is not None
    primary = picked[1][0]
    result = KGMatchService().resolve_scope(edital_id=primary, max_analogues=3)
    assert result[0] == primary
    assert len(result) <= 4
