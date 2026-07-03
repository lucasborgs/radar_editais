"""Testes do OpportunityService (pipeline 3 tiers de descoberta).

A pipeline lê dos hipergrados em disco — sem mock de dados. Os asserts
verificam shape/estrutura, não conteúdo específico."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.kg import hypergraph_catalog
from core.services.opportunity_service import OpportunityService

# TestExplore roda o pipeline real contra os hipergrados EM DISCO
# (data/knowledge_graph/hypergraphs/), que NÃO é versionado — durabilidade via
# kg_store/Postgres. Num checkout limpo (CI) o corpus está vazio: explore()
# retorna {} (o _merge omite categorias vazias) e os asserts de shape quebram.
# Pulamos os end-to-end quando não há corpus; os testes de shape dos tiers
# (toleram vazio) e o TestMerge unitário seguem cobrindo a lógica.
_HAS_CORPUS = bool(hypergraph_catalog.list_editais(limit=1))
_needs_corpus = pytest.mark.skipif(
    not _HAS_CORPUS,
    reason="sem corpus de hipergrafos em disco (não versionado; CI/data-less)",
)

# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def svc():
    return OpportunityService()


# ===========================================================================
# Tier 1 — lexical match
# ===========================================================================

class TestTier1Lexical:
    def test_returns_all_categories(self, svc):
        result = svc._tier1_lexical("agro", top_k=5)
        assert isinstance(result, dict)
        for cat in ("editais", "icts", "investidores", "programas", "temas"):
            assert cat in result, f"missing category: {cat}"
            assert isinstance(result[cat], list), f"{cat} should be a list"

    def test_empty_query_returns_empty(self, svc):
        result = svc._tier1_lexical("", top_k=5)
        assert all(len(v) == 0 for v in result.values())

    def test_unmatched_query_returns_no_temas(self, svc):
        result = svc._tier1_lexical("zzzxywvnonexistent", top_k=5)
        assert len(result["temas"]) == 0

    def test_top_k_limits_editais(self, svc):
        full = svc._tier1_lexical("", top_k=1000)
        capped = svc._tier1_lexical("", top_k=3)
        # temas podem ser menos que 3; só verifica se não estoura
        assert len(capped["editais"]) <= 3 or len(capped["editais"]) == len(full["editais"])


# ===========================================================================
# Tier 2 — cross-source
# ===========================================================================

class TestTier2Cross:
    def test_adds_entities_from_all_categories(self, svc):
        t1 = svc._tier1_lexical("inteligência artificial", top_k=5)
        t2 = svc._tier2_cross(t1)
        for cat in ("editais", "icts", "investidores", "programas"):
            assert cat in t2, f"missing category: {cat}"
            assert isinstance(t2[cat], list)

    def test_cross_returns_dicts_with_name_key(self, svc):
        t1 = svc._tier1_lexical("inteligência artificial", top_k=5)
        t2 = svc._tier2_cross(t1)
        for cat in ("editais", "icts", "investidores", "programas"):
            if t2[cat]:
                assert "name" in t2[cat][0], f"{cat} item missing 'name'"

    def test_cross_no_temas_is_noop(self, svc):
        t1 = {"temas": []}
        t2 = svc._tier2_cross(t1)
        assert all(len(v) == 0 for v in t2.values())


# ===========================================================================
# Merge
# ===========================================================================

class TestMerge:
    def test_dedup_same_name_in_t1_and_t2(self, svc):
        t1 = {"editais": [{"name": "Edital A", "title": ""}]}
        t2 = {"editais": [{"name": "Edital A", "source": "cross"}]}
        merged = svc._merge(t1, t2, None)
        assert len(merged["editais"]) == 1

    def test_t3_none_is_safe(self, svc):
        t1 = {"editais": [{"name": "Edital A"}]}
        t2 = {"editais": []}
        merged = svc._merge(t1, t2, None)
        assert len(merged["editais"]) == 1

    def test_t3_embed_adds(self, svc):
        t1 = {"editais": [{"name": "Edital A"}]}
        t2 = {"editais": []}
        t3 = {"editais": [{"name": "Edital B", "source": "embed"}]}
        merged = svc._merge(t1, t2, t3)
        assert len(merged["editais"]) == 2

    def test_dedup_across_all_three_tiers(self, svc):
        t1 = {"editais": [{"name": "Edital X"}]}
        t2 = {"editais": [{"name": "Edital X", "source": "cross"}]}
        t3 = {"editais": [{"name": "Edital X", "source": "embed"}]}
        merged = svc._merge(t1, t2, t3)
        assert len(merged["editais"]) == 1


# ===========================================================================
# Explore (end-to-end)
# ===========================================================================

@_needs_corpus
class TestExplore:
    def test_explore_returns_expected_structure(self, svc):
        result = svc.explore("inteligência artificial", top_k=5)
        assert isinstance(result, dict)
        for cat in ("editais", "icts", "investidores", "programas"):
            assert cat in result, f"missing category: {cat}"

    def test_explore_returns_strings_in_items(self, svc):
        result = svc.explore("inteligência artificial", top_k=5)
        for cat in ("editais", "icts", "investidores", "programas"):
            for item in result[cat]:
                name = item.get("name", item.get("title", ""))
                assert isinstance(name, str) and len(name) > 0

    def test_explore_known_theme_returns_results(self, svc):
        result = svc.explore("agro", top_k=5)
        assert sum(len(result[k]) for k in result) > 0

    def test_explore_tier3_merge_with_tier1(self, svc):
        """Verifica que _merge com t3=None não quebra — t3 depende de LLM e
        não roda em CI sem chave."""
        t1 = svc._tier1_lexical("agro", top_k=5)
        t2 = svc._tier2_cross(t1)
        merged = svc._merge(t1, t2, None)
        assert isinstance(merged, dict)
        # merge só retorna categorias com ao menos 1 item
        for k in ("editais", "icts", "investidores", "programas"):
            if k in merged:
                assert len(merged[k]) > 0

    def test_explore_top_k_limits_results(self, svc):
        small = svc.explore("agro", top_k=3)
        large = svc.explore("agro", top_k=30)
        for cat in ("editais", "icts", "investidores", "programas"):
            assert len(small[cat]) <= len(large[cat]) or len(large[cat]) == 0
