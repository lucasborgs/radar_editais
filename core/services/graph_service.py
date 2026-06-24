"""
GraphService — Leitura do grafo Obsidian (vault .md + wikilinks).

Sem LLM, sem API keys. Extraído de KGMatchService (Fase 0 da spec
match-evolution.md). Usa cache em memória com invalidação por mtime.
"""
from __future__ import annotations

import functools
import logging
import re
from pathlib import Path

from config import OBSIDIAN_VAULT_DIR
from core.kg import kg_store
from core.kg.edital_id import id_to_slug, slug_to_id

logger = logging.getLogger(__name__)

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


class GraphService:
    """Leitura do grafo Obsidian (vault .md + wikilinks). Sem LLM, sem API keys."""

    def __init__(self, vault: Path = OBSIDIAN_VAULT_DIR):
        self.vault = vault

    # ------------------------------------------------------------------
    # Cache do grafo
    # ------------------------------------------------------------------

    @staticmethod
    def _vault_mtime_hash(vault: Path) -> int:
        """Latest mtime across all .md files (nanosecond precision).
        Used as cache key for get_graph()."""
        try:
            return max(
                (p.stat().st_mtime_ns for p in vault.rglob("*.md")),
                default=vault.stat().st_mtime_ns,
            )
        except OSError:
            return 0

    @functools.lru_cache(maxsize=1)
    def _build_graph(self, _mtime_key: int) -> dict:
        """Constructs the graph from the vault. Cached by mtime hash."""
        vault = self.vault
        if not vault.exists():
            logger.warning("Vault Obsidian não encontrado: %s", vault)
            return {"nodes": [], "links": []}

        nodes: dict[str, dict] = {}
        edges: set[tuple[str, str]] = set()

        for path in sorted(vault.rglob("*.md")):
            rel = path.relative_to(vault).with_suffix("")
            node_id = "/".join(rel.parts)
            ntype = self._node_type_for_parts(rel.parts)
            if ntype is None:
                continue

            text = path.read_text(encoding="utf-8")
            fm = self._parse_frontmatter(text)
            label = "Radar de Editais" if ntype == "home" else (fm.get("title") or path.stem)
            node: dict = {
                "id": node_id,
                "type": ntype,
                "label": label,
            }
            if ntype == "edital":
                node["edital_id"] = fm.get("chamada_id") or path.stem
                node["status"] = fm.get("status", "Desconhecido")
            nodes[node_id] = node

            for target, _alias in _WIKILINK_RE.findall(text):
                target = target.strip()
                if target.endswith("/"):
                    continue
                if self._node_type_for_parts(tuple(target.split("/"))) is None:
                    continue
                a, b = sorted((node_id, target))
                edges.add((a, b))

        for a, b in edges:
            for nid in (a, b):
                if nid not in nodes:
                    seg = tuple(nid.split("/"))
                    nodes[nid] = {
                        "id": nid,
                        "type": self._node_type_for_parts(seg) or "outro",
                        "label": seg[-1],
                    }

        links = [{"source": a, "target": b} for a, b in sorted(edges)]
        return {"nodes": list(nodes.values()), "links": links}

    def get_graph(self) -> dict:
        """Nós + arestas do knowledge graph. Cache em memória invalidado por mtime."""
        return self._build_graph(self._vault_mtime_hash(self.vault))

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_frontmatter(self, text: str) -> dict:
        """Parse mínimo do YAML frontmatter — só os escalares que usamos."""
        m = _FRONTMATTER_RE.match(text)
        if not m:
            return {}
        fm: dict = {}
        for line in m.group(1).splitlines():
            if ":" not in line or line.lstrip().startswith("-"):
                continue
            key, _, val = line.partition(":")
            val = val.strip().strip('"').strip("'")
            if val:
                fm[key.strip()] = val
        return fm

    def _folder_type_map(self) -> dict[str, str]:
        """folder (plural, no vault) → tipo de nó (chave do schema §6.1)."""
        from core.kg import wiki_schema
        return {
            v["folder"]: k
            for k, v in wiki_schema.node_types().items()
            if v.get("folder")
        }

    def _node_type_for_parts(self, parts: tuple[str, ...]) -> str | None:
        """parts = node_id.split('/'). Retorna tipo ou None (fora do schema)."""
        if len(parts) < 3:
            return "home" if parts[-1] == "HOME" else None
        return self._folder_type_map().get(parts[1])

    # ------------------------------------------------------------------
    # Navegação do grafo
    # ------------------------------------------------------------------

    def edital_ids_for_node(self, node_id: str) -> list[str]:
        """Extrai IDs de editais ligados ao nó via wikilinks no MD do vault."""
        path = self.vault / f"{node_id}.md"
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        ids = []
        for target, _ in _WIKILINK_RE.findall(text):
            parts = target.strip().split("/")
            if len(parts) >= 2 and parts[-2] == "editais":
                ids.append(slug_to_id(parts[-1]))
        return ids

    def find_analogue_ids(self, edital_id: str) -> list[str]:
        """Traversal reverso: edital → temas/publicos/subprogramas → editais análogos."""
        edital_path = self.vault / f"radar-editais/editais/{id_to_slug(edital_id)}.md"
        if not edital_path.exists():
            return []

        text = edital_path.read_text(encoding="utf-8")
        folder_type = self._folder_type_map()

        neighbour_nodes: list[str] = []
        for target, _ in _WIKILINK_RE.findall(text):
            target = target.strip()
            if target.endswith("/"):
                continue
            parts = target.split("/")
            if len(parts) < 3:
                continue
            folder = parts[1]
            if folder == "editais" or folder not in folder_type:
                continue
            neighbour_nodes.append(target)

        seen: set[str] = {str(edital_id)}
        analogues: list[str] = []
        for node_id in neighbour_nodes:
            for eid in self.edital_ids_for_node(node_id):
                if eid not in seen:
                    seen.add(eid)
                    analogues.append(eid)
        return analogues

    def resolve_scope(
        self,
        edital_id: str | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
        max_analogues: int = 3,
    ) -> list[str]:
        """Resolve trigger → list[edital_ids], com o ID primário primeiro.

        Regras:
          - node_type ∈ {tema, publico, subprograma, fonte, ...}: retorna os IDs
            que o nó liga via wikilinks (`edital_ids_for_node`)
          - node_type == "edital" ou edital_id (sessão): retorna [primary] +
            análogos (até max_analogues) via traversal reverso
          - Sem trigger algum: retorna todos os edital_ids do índice
        """
        if node_id and node_type and node_type not in ("edital", "home", None):
            return self.edital_ids_for_node(node_id)

        primary = edital_id
        if node_id and node_type == "edital":
            primary = slug_to_id(node_id.split("/")[-1])

        if primary:
            analogues = self.find_analogue_ids(primary)[:max_analogues]
            return [primary] + analogues

        index = kg_store.load_index()
        return [str(e["id"]) for e in index.get("editais", [])]
