"""
Testes de `KGMatchService.resolve_scope` — trigger → list[edital_ids].

Os testes usam o vault REAL do projeto (`obsidian_vault/radar-editais/`) em vez
de mocks: a lógica é traversal de wikilinks em arquivos .md, então o vault
sintetizado seria fluxo paralelo de manutenção. Mudanças no conteúdo do vault
podem exigir ajustes nos asserts mais específicos (ordem/lista exata).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import OBSIDIAN_VAULT_DIR  # noqa: E402
from core.kg_match_service import KGMatchService  # noqa: E402


# =============================================================================
# Helpers
# =============================================================================

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _first_edital_in_tema(slug: str) -> str:
    """Lê o .md do tema e devolve o ID do primeiro edital wikilinkado."""
    path = OBSIDIAN_VAULT_DIR / f"radar-editais/temas/{slug}.md"
    text = path.read_text(encoding="utf-8")
    for target in _WIKILINK_RE.findall(text):
        parts = target.strip().split("/")
        if len(parts) >= 2 and parts[-2] == "editais":
            return parts[-1]
    raise AssertionError(f"Nenhum edital encontrado em temas/{slug}.md")


# =============================================================================
# Tests
# =============================================================================

def test_resolve_scope_node_tema():
    """Clique num tema → todos os editais que ele lista, sem análogos."""
    svc = KGMatchService()
    result = svc.resolve_scope(
        node_id="radar-editais/temas/energia-e-transicao-sustentavel",
        node_type="tema",
    )
    # A ordem segue a aparição no .md do tema (601, 771, 602 conforme vault).
    assert result == ["601", "771", "602"]


def test_resolve_scope_edital_node_includes_analogues():
    """Clique num nó edital → primário + análogos via traversal reverso."""
    svc = KGMatchService()
    result = svc.resolve_scope(
        node_id="radar-editais/editais/601",
        node_type="edital",
        max_analogues=3,
    )
    assert result[0] == "601"
    assert len(result) <= 4
    # 601 está só no cluster de energia (3 editais), então análogos ⊆ {771, 602}.
    energia_cluster = {"771", "602"}
    assert any(eid in energia_cluster for eid in result[1:])


def test_resolve_scope_edital_id_session():
    """Sessão com edital_id deve produzir mesmo resultado do clique no nó."""
    svc = KGMatchService()
    via_session = svc.resolve_scope(edital_id="601")
    via_node = svc.resolve_scope(
        node_id="radar-editais/editais/601",
        node_type="edital",
    )
    assert via_session == via_node


def test_resolve_scope_edital_sem_vault_page():
    """ID inexistente no vault não deve crashar — só retorna o próprio ID."""
    svc = KGMatchService()
    result = svc.resolve_scope(edital_id="9999")
    assert result == ["9999"]


def test_resolve_scope_free_trigger():
    """Sem trigger algum → todos os editais do índice."""
    svc = KGMatchService()
    result = svc.resolve_scope()
    assert len(result) > 0
    # Todos os elementos viram strings (estável pra consumidores).
    assert all(isinstance(eid, str) for eid in result)


def test_resolve_scope_max_analogues_cap():
    """Cluster grande (agro, 9 editais) ainda respeita o cap em max_analogues."""
    svc = KGMatchService()
    primary = _first_edital_in_tema("agro-bioeconomia-e-alimentos")
    result = svc.resolve_scope(edital_id=primary, max_analogues=3)
    assert result[0] == primary
    assert len(result) <= 4
