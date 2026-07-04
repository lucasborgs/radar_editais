"""Unified Opportunity Service — pipeline de retrieval em 3 tiers.

Consulta o ecossistema de inovação (editais, ICTs, investidores, programas)
por tema, combinando busca lexical, travessia cross-source e match por
embedding. Substitui `oportunidades_por_tema` como ponto de entrada único
para descoberta no ExploreAgent.

Fiel ao Hyper-Extract: cada subgrafo é um KA independente. A conexão entre
eles é resolvida em tempo de query via entity index (type, name).

Tiers paralelos (asyncio.gather):
  Tier 1 — Léxico: varre name+description de todos os nós com _theme_match
  Tier 2 — Cross-source: BFS multi-subgrafo a partir dos nós match do Tier 1
  Tier 3 — Embedding: match geométrico (só se houver perfil da empresa)
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from core.kg import hypergraph_catalog
from core.llm.agent_tools.explore_tools import (
    build_entity_index,
    neighborhood,
)

logger = logging.getLogger(__name__)


def _flatten(items):
    """Achata lista de listas."""
    for x in items:
        yield from x


class OpportunityService:
    """Pipeline de descoberta. Uma instância por turno do agente —
    cacheia o grafo e o entity index para múltiplas chamadas."""

    def __init__(self):
        self._graphs: dict[str, dict] | None = None
        self._entity_index: dict[tuple[str, str], list[tuple[str, dict]]] | None = None
        self._eco_nodes: list[tuple[str, dict]] | None = None

    def _ensure_graphs(self):
        if self._graphs is None:
            from core.kg import kg_store
            self._graphs = kg_store.load_all_hypergraphs()
            self._entity_index = build_entity_index(self._graphs)
            self._eco_nodes = [
                (fk, n) for fk, g in self._graphs.items() for n in g.get("nodes", [])
            ]

    # ── Tier 1 — Léxico (~10ms) ─────────────────────────────────────────

    def _tier1_lexical(self, tema: str, top_k: int) -> dict:
        """Busca lexical por nome/descrição dos nós + catálogo de entidades."""
        result: dict[str, list] = {
            "editais": [],
            "icts": [],
            "investidores": [],
            "programas": [],
            "temas": [],
        }
        if not tema.strip():
            return result

        # Editais via catalog (já tem _theme_match interno)
        editais = hypergraph_catalog.list_editais(limit=top_k)
        editais = [e for e in editais if hypergraph_catalog._theme_match(tema, e.get("themes", []))]
        result["editais"] = editais[:top_k]

        # ICTs/Investidores/Programas via catalog hypergraphs
        for ck, key in [("ict", "icts"), ("investidores", "investidores"), ("programas", "programas")]:
            result[key] = hypergraph_catalog.list_entity_catalog(
                ck, tema=tema, limit=top_k + 10,
            )[:top_k]

        # Nós Tema/Tecnologia/Aplicação varrendo todos os subgrafos (nome match)
        seen_themes: dict[str, set[str]] = defaultdict(set)
        for fk, n in (self._eco_nodes or []):
            # Conteúdo temático v2 = Conceito (exceto ex-Entidade inerte, entidade_v1).
            if n.get("type") != "Conceito" or n.get("origem") == "entidade_v1":
                continue
            nm = n.get("name", "")
            if hypergraph_catalog._theme_match(tema, [nm]):
                seen_themes[nm].add(fk.split("__")[0])

        for name, fontes in sorted(seen_themes.items()):
            result["temas"].append({
                "name": name,
                "fontes": sorted(fontes),
                "type": "Conceito" if name else "",
            })
            if len(result["temas"]) >= top_k:
                break

        return result

    # ── Tier 2 — Travessia cross-source (~50ms) ──────────────────────────

    def _tier2_cross(self, tier1: dict, depth: int = 2) -> dict:
        """Para cada nó temático encontrado no Tier 1, roda neighborhood
        cross-source e coleta conexões indiretas (aditivo ao Tier 1)."""
        extra: dict[str, list[dict]] = {
            "editais": [],
            "icts": [],
            "investidores": [],
            "programas": [],
        }
        seen: dict[str, set[str]] = {k: set() for k in extra}
        for t in tier1.get("temas", []):
            name = t.get("name", "")
            if not name:
                continue
            out = neighborhood(
                self._graphs, name, depth=depth,
                max_edges=10, cross_source=True, entity_index=self._entity_index,
            )
            for line in out.split("\n"):
                text = line.strip()
                # Marcadores v2: o rótulo de membro é `nome (Type/kind)` (ver
                # explore_tools._member_label) — ICT/Investidor são ambos Ator,
                # distintos só pelo kind.
                for marker, key in [
                    ("(Oportunidade/edital)", "editais"),
                    ("(Ator/ict)", "icts"),
                    ("(Ator/investidor)", "investidores"),
                    ("(Oportunidade/programa)", "programas"),
                ]:
                    if marker not in text:
                        continue
                    # Extract name between prefix/arrow/bullet and the (Type) suffix
                    name = (
                        text.split("(")[0]
                        .split(":")[-1]
                        .replace("·", "")
                        .replace("###", "")
                        .replace("↳", "")
                        .strip()
                    )
                    if name and name not in seen[key]:
                        seen[key].add(name)
                        extra[key].append({"name": name, "source": "cross"})
                    break
        return extra

    # ── Tier 3 — Embedding match (~500ms, condicional) ──────────────────

    def _tier3_embed(self, company_nodes: list[dict], top_k: int) -> dict:
        """Match geométrico: empresa → aresta sintética → ecossistema."""
        from core.services.hypergraph_match import (
            find_matching_editais,
            find_matching_entities,
        )
        result: dict = {"editais": [], "icts": [], "investidores": [], "programas": []}

        editais = find_matching_editais(
            company_nodes, top_k=top_k, ecosystem=self._eco_nodes,
        )
        result["editais"] = [
            {"id": f"{e.source}:{e.edital_id}", "name": e.name,
             "affinity": e.affinity, "score": e.score, "source": "embedding"}
            for e in editais
        ]

        entities = find_matching_entities(
            company_nodes, top_k=top_k, ecosystem=self._eco_nodes,
        )
        for ent in entities:
            key = {
                "ict": "icts",
                "investidor": "investidores",
                "programa": "programas",
            }.get(ent.kind)
            if key:
                result[key].append({
                    "id": ent.name.lower(),
                    "name": ent.name,
                    "affinity": ent.affinity,
                    "score": ent.score,
                    "source": "embedding",
                })
        return result

    # ── Merge ───────────────────────────────────────────────────────────

    def _merge(self, t1: dict, t2: dict, t3: dict | None) -> dict:
        """Merge dos 3 tiers com dedup."""
        seen: dict[str, set[str]] = defaultdict(set)
        merged: dict[str, list] = defaultdict(list)

        for tier, _source in [(t1, "lex"), (t2, "cross"), (t3, "embed")]:
            if tier is None:
                continue
            for category in ("editais", "icts", "investidores", "programas"):
                for item in tier.get(category, []):
                    name = item.get("name", "") or item.get("title", "")
                    if not name:
                        continue
                    if name not in seen[category]:
                        seen[category].add(name)
                        merged[category].append(item)

        return dict(merged)

    # ── Public API ──────────────────────────────────────────────────────

    def explore(
        self,
        tema: str,
        *,
        top_k: int = 15,
        company_nodes: list[dict] | None = None,
    ) -> dict:
        """Pipeline completa de descoberta. Síncrona (chama asyncio.run
        internamente para paralelizar tiers). Retorna dict com:
        {editais, icts, investidores, programas, temas}."""
        self._ensure_graphs()

        # Tiers paralelos (assíncronos)
        async def _run():
            t1 = asyncio.to_thread(self._tier1_lexical, tema, top_k)
            t2_coro = self._tier2_cross  # precisa do resultado de t1
            t3 = (
                asyncio.to_thread(self._tier3_embed, company_nodes, top_k)
                if company_nodes else None
            )
            t1_result = await t1
            t2 = asyncio.create_task(
                asyncio.to_thread(t2_coro, t1_result)
            )
            tasks = [t2]
            if t3:
                tasks.append(t3)
            t2_result, *t3_results = await asyncio.gather(*tasks)
            t3_result = t3_results[0] if t3_results else None
            return self._merge(t1_result, t2_result, t3_result)

        return asyncio.run(_run())
