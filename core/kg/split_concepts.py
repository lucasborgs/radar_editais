"""core/kg/split_concepts.py — granularidade atômica dos Conceitos (KG v2 resíduos PR-D).

Spec docs/specs/kg-v2-residuos.md (PR-D). Os Conceitos com 5+ palavras (507
names, 29% do corpus) são decompostos em termos atômicos (≤3 palavras) por LLM.
Nós novos herdam dim + arestas do original.

O validador do PR-B roda sobre o resultado (split gera nós novos que precisam
de higiene). Re-embed acontece naturalmente (hash do texto muda).

Passos:
1. INVENTORY — identifica Conceitos com ≥ max_words (default 5)
2. SPLIT — LLM decompõe cada name em termos atômicos (R7)
3. APPLY — reescreve hipergrados: nó original removido, nós novos inseridos,
   arestas re-apontadas
4. CANONICALIZE — re-valida nós novos contra o concept_canon do PR-B
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

SPLIT_MODEL = "gpt-4o-mini"
_DEFAULT_MAX_WORDS = 5


# ---------------------------------------------------------------------------
# Inventário
# ---------------------------------------------------------------------------

def _word_count(name: str) -> int:
    return len(name.strip().split())


def inventory_long_concepts(
    graphs: dict[str, dict], *, max_words: int = _DEFAULT_MAX_WORDS,
) -> dict[str, dict]:
    """Inventário de Conceitos com ≥ max_words palavras.

    Retorna {id: {id, name, dim, description, files, fan_in, word_count}}.
    Só ids com word_count ≥ max_words entram. Cada id aparece uma vez
    (mesmo conceito = mesmo id em qualquer subgrafo).
    """
    inv: dict[str, dict] = {}
    for fk, g in graphs.items():
        for n in g.get("nodes", []):
            if n.get("type") != "Conceito" or not n.get("id"):
                continue
            name = n.get("name") or ""
            wc = _word_count(name)
            if wc < max_words:
                continue
            nid = n["id"]
            e = inv.setdefault(nid, {
                "id": nid, "name": name, "dim": n.get("dim", "tema"),
                "description": "", "files": [], "fan_in": 0, "word_count": wc,
            })
            if fk not in e["files"]:
                e["files"].append(fk)
            e["fan_in"] = len(e["files"])
            desc = n.get("description") or ""
            if len(desc) > len(e["description"]):
                e["description"] = desc
    return inv


# ---------------------------------------------------------------------------
# Split via LLM
# ---------------------------------------------------------------------------

_SPLIT_SYSTEM = """Você é curador de vocabulário do grafo de conhecimento do ecossistema \
brasileiro de fomento à inovação. Recebe um CONCEITO (termo temático) que tem MAIS DE 5 \
PALAVRAS e precisa decompô-lo em conceitos ATÔMICOS.

Regras (R7):
1. Termo atômico = até 3 palavras, salvo nome próprio consagrado ("Lei do Bem",
   "Marco Civil da Internet", "Indústria 4.0", "Internet das Coisas").
2. Compostos "X e Y" ou "X, Y" viram DOIS conceitos separados.
3. Se o termo já é atômico (≤3 palavras ou nome próprio), mantenha como está.
4. Se o termo tem um núcleo principal + especificador, mantenha o especificador
   ("agricultura de baixo carbono" → "agricultura de baixo carbono", não só
   "agricultura").
5. A descrição do conceito original é herdada por todos os splits (não a repita).

Exemplos:
  Entrada: "agricultura de baixo carbono e uso eficiente de recursos"
  → ["agricultura de baixo carbono", "uso eficiente de recursos"]

  Entrada: "Programa Nacional de Máquinas, Equipamentos e Implementos"
  → ["máquinas agrícolas", "equipamentos agrícolas", "implementos agrícolas"]

  Entrada: "Pesquisa Aplicada em Centros Temáticos 2025"
  → ["pesquisa aplicada"]  (o nome do edital, não um conceito genuíno)

  Entrada: "desenvolvimento de soluções para a cadeia do Biogás e Biometano"
  → ["soluções para biogás", "soluções para biometano"]

Responda SEMPRE JSON: {"splits": [{"nome": "...", "dim": "tema|tecnologia|aplicacao"}]}
- Se o conceito tem ≤3 palavras OU é nome próprio, devolva UM split com o mesmo nome.
- Se o conceito é decomposto, devolva UM split por termo atômico.
- O dim deve refletir a dimensão do termo atômico (pode herdar do original)."""


def propose_splits(
    inventory: dict[str, dict], *, client=None, model: str | None = None,
) -> dict[str, list[dict]]:
    """LLM: id → [{nome, dim}] (os splits do conceito original).

    Retorna o PLANO: {old_id: [{"nome": "...", "dim": "..."}, ...]}.
    Cada old_id produz ≥1 split. Singleton = mantido como está.
    """
    if client is None:
        from core.llm.llm_client import make_client
        client = make_client(max_retries=3)
    model = model or SPLIT_MODEL

    plan: dict[str, list[dict]] = {}
    for nid, c in inventory.items():
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SPLIT_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps({
                            "id": nid,
                            "nome": c["name"],
                            "dim": c["dim"],
                            "descricao": (c.get("description") or "")[:300],
                        }, ensure_ascii=False),
                    },
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            out = json.loads(resp.choices[0].message.content).get("splits", [])
        except Exception as e:
            logger.warning("split_concepts: falha no split de %s (%s) — mantendo original", nid, e)
            out = [{"nome": c["name"], "dim": c["dim"]}]

        plan[nid] = [
            {"nome": s["nome"].strip(), "dim": s.get("dim") or c["dim"]}
            for s in out if s.get("nome") and s["nome"].strip()
        ] or [{"nome": c["name"], "dim": c["dim"]}]
    return plan


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_splits(
    graphs: dict[str, dict], plan: dict[str, list[dict]],
) -> tuple[dict[str, dict], dict[str, int]]:
    """Aplica o plano de splits aos hipergrados.

    Para cada nó Conceito cujo id está no plano:
    - Remove o nó original
    - Cria nós novos (um por split), herdando a description do original
    - Re-aponta arestas que referenciavam o id original para os novos ids
    - Arestas com <2 membros são removidas (degeneradas pelo split)

    Retorna (graphs_modificados, stats).
    """
    from core.kg.schema import slugify

    st: dict[str, int] = {
        "conceitos_split": 0,
        "conceitos_criados": 0,
        "arestas_reatadas": 0,
        "arestas_removidas": 0,
    }

    for fk, g in graphs.items():
        interrompidos: set[str] = set()  # nós a remover
        novos_por_original: dict[str, list[dict]] = {}  # old_id → [new_nodes]

        for n in g.get("nodes", []):
            nid = n.get("id") or ""
            if nid not in plan:
                continue
            splits = plan[nid]
            interrompidos.add(nid)
            novos: list[dict] = []
            for s in splits:
                new_id = f"con:{slugify(s['nome'])}"
                novos.append({
                    "id": new_id,
                    "type": "Conceito",
                    "dim": s["dim"],
                    "name": s["nome"],
                    "description": n.get("description", ""),
                })
            novos_por_original[nid] = novos
            st["conceitos_split"] += 1
            st["conceitos_criados"] += len(novos)

        if not interrompidos:
            continue

        novos_nodes: list[dict] = [
            n for n in g.get("nodes", []) if n.get("id") not in interrompidos
        ]
        # Adiciona os splits (dedup por id já existente)
        seen_ids: set[str] = {n["id"] for n in novos_nodes if n.get("id")}
        for novos in novos_por_original.values():
            for n in novos:
                if n["id"] not in seen_ids:
                    seen_ids.add(n["id"])
                    novos_nodes.append(n)

        novas_arestas: list[dict] = []
        for e in g.get("edges", []):
            members = e.get("members", [])
            novos_members: list[str] = []
            for m in members:
                if m in interrompidos:
                    novos_members.extend(
                        n["id"] for n in novos_por_original.get(m, [])
                    )
                else:
                    novos_members.append(m)
            novos_members = list(dict.fromkeys(novos_members))  # dedup
            if len(novos_members) >= 2:
                novas_arestas.append({**e, "members": novos_members})
                st["arestas_reatadas"] += 1 if any(m in interrompidos for m in e.get("members", [])) else 0
            else:
                st["arestas_removidas"] += 1

        graphs[fk] = {**g, "nodes": novos_nodes, "edges": novas_arestas}

    return graphs, st


def canonicalize_after_split(
    graphs: dict[str, dict], *, llm_new: bool = False,
) -> dict[str, dict]:
    """Re-valida os nós novos contra o concept_canon do PR-B.

    Com llm_new=False (default CI), só reaplica o canon map existente
    (determinístico). Os nós novos gerados pelo split que já existiam no
    canon (ex.: "agricultura de baixo carbono" já validado) são tratados
    pelo replay. Inéditos passam pelo validador se llm_new=True.
    """
    from core.kg.canonicalize import canonicalize_fresh_graph

    for fk in list(graphs):
        graphs[fk] = canonicalize_fresh_graph(graphs[fk], file_key=fk, llm_new=llm_new)
    return graphs
