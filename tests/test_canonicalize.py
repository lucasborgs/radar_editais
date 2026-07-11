"""Testes do apply mecânico da higiene de Conceitos (core/kg/canonicalize, PR3).

Cobrem só a metade PURA (inventário, compilação do canon, canonicalize_graph,
macro_temas) — as funções propose-* usam LLM/embeddings e ficam fora da CI
(mesmo padrão do hyper_extractor). A segunda demão de higiene (KG v2 resíduos
PR-B) acrescenta lógica DETERMINÍSTICA (padrões anti-classe, chave de variante,
auto-merge da banda >0.90) que é pura e entra aqui."""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.kg.canonicalize import (
    CanonStats,
    _auto_variant_merges,
    _variant_key,
    anti_class_verdict,
    apply_macro_temas,
    build_canon,
    canonicalize_graph,
    corpus_stats,
    inventory_concepts,
    propose_validation,
)

# Subgrafo v2 sintético: 1 edital, 1 ICT, 4 Conceitos (1 ruído-geografia,
# 1 ex-Entidade legítimo, 2 duplicatas semânticas ML/aprendizado de máquina).
_G = {
    "format_version": 2,
    "source_hash": "h",
    "proveniencia": {},
    "nodes": [
        {"id": "op:edital-x", "type": "Oportunidade", "kind": "edital", "name": "Edital X"},
        {"id": "ator:senai", "type": "Ator", "kind": "ict", "name": "SENAI"},
        {"id": "con:brasil", "type": "Conceito", "dim": "tema", "name": "Brasil"},
        {"id": "con:bioinsumos", "type": "Conceito", "dim": "tema", "name": "bioinsumos",
         "origem": "entidade_v1"},
        {"id": "con:ml", "type": "Conceito", "dim": "tecnologia", "name": "ML",
         "description": "sigla"},
        {"id": "con:aprendizado-de-maquina", "type": "Conceito", "dim": "tecnologia",
         "name": "aprendizado de máquina", "description": "descrição bem mais longa aqui"},
    ],
    "edges": [
        {"type": "abrange_tema", "members": ["op:edital-x", "con:brasil"]},
        {"type": "abrange_tema", "members": ["op:edital-x", "con:bioinsumos"]},
        {"type": "viabiliza", "members": ["ator:senai", "con:ml", "con:aprendizado-de-maquina"]},
    ],
}

_CANON = build_canon(
    validation_plan={
        "con:brasil": {"veredicto": "descartar", "categoria": "geografia"},
        "con:bioinsumos": {"veredicto": "manter", "promote": True},
        "con:ml": {"veredicto": "manter"},
        "con:aprendizado-de-maquina": {"veredicto": "manter"},
    },
    merge_plan={
        "clusters": [
            {
                "members": ["con:ml", "con:aprendizado-de-maquina"],
                "score_medio": 0.91,
                "grupos": [
                    {"membros": ["con:ml", "con:aprendizado-de-maquina"],
                     "nome_canonico": "aprendizado de máquina", "dim": "tecnologia"},
                ],
            }
        ]
    },
)


def test_build_canon_compiles_plans():
    assert _CANON["discards"] == {"con:brasil": "geografia"}
    assert _CANON["promotions"] == ["con:bioinsumos"]
    # os DOIS membros do grupo apontam para a mesma forma canônica
    assert _CANON["aliases"]["con:ml"]["name"] == "aprendizado de máquina"
    assert _CANON["aliases"]["con:aprendizado-de-maquina"]["name"] == "aprendizado de máquina"


def test_canonicalize_graph_applies_canon():
    st = CanonStats()
    g2 = canonicalize_graph(_G, _CANON, stats=st)
    ids = {n["id"] for n in g2["nodes"]}

    # descarte: geografia some do grafo e da aresta (aresta degenerada cai)
    assert "con:brasil" not in ids
    assert st.discarded == 1 and st.dropped_edges == 1

    # promoção: ex-Entidade perde a marca (entra na afinidade)
    bio = next(n for n in g2["nodes"] if n["id"] == "con:bioinsumos")
    assert "origem" not in bio

    # merge: ML e aprendizado de máquina viram UM nó com id canônico,
    # ficando a descrição mais longa; a aresta re-aponta sem duplicar membro
    assert "con:ml" not in ids
    ml = next(n for n in g2["nodes"] if n["id"] == "con:aprendizado-de-maquina")
    assert ml["name"] == "aprendizado de máquina"
    assert ml["description"] == "descrição bem mais longa aqui"
    assert st.deduped == 1
    via = next(e for e in g2["edges"] if e["type"] == "viabiliza")
    assert via["members"] == ["ator:senai", "con:aprendizado-de-maquina"]

    # não-Conceito passam intactos
    assert {"op:edital-x", "ator:senai"} <= ids


def test_canonicalize_graph_is_idempotent():
    g2 = canonicalize_graph(_G, _CANON)
    g3 = canonicalize_graph(g2, _CANON)
    assert g3 == g2


def test_canonicalize_graph_retypes_ator():
    canon = build_canon(
        {"con:bioinsumos": {"veredicto": "ator", "ator_kind": "corporate"}}, None,
    )
    g2 = canonicalize_graph(_G, canon)
    node = next(n for n in g2["nodes"] if n["name"] == "bioinsumos")
    assert node["type"] == "Ator" and node["kind"] == "corporate"
    assert node["id"] == "ator:bioinsumos" and "dim" not in node and "origem" not in node
    # a aresta segue o novo id
    edge = next(e for e in g2["edges"] if e["type"] == "abrange_tema"
                and "ator:bioinsumos" in e["members"])
    assert edge["members"] == ["op:edital-x", "ator:bioinsumos"]


def test_inventory_aggregates_cross_file():
    g_b = {
        "format_version": 2,
        "nodes": [{"id": "con:ml", "type": "Conceito", "dim": "tema", "name": "ML",
                   "description": "descrição mais longa no arquivo B"}],
        "edges": [],
    }
    inv = inventory_concepts({"a": _G, "b": g_b})
    ml = inv["con:ml"]
    assert ml["fan_in"] == 2
    # dim = moda (empate 1-1 resolve pelo mais comum retornado pelo Counter);
    # description = a mais longa entre as instâncias
    assert ml["description"] == "descrição mais longa no arquivo B"
    assert inv["con:bioinsumos"]["ex_entidade"] is True


def test_corpus_stats_fan_in():
    s = corpus_stats({"a": _G})
    assert s["conceitos_ids_unicos"] == 4
    assert s["fan_in_medio"] == 1.0
    assert s["conceitos_ex_entidade"] == 1


def test_apply_macro_temas():
    graphs = {"a": {"nodes": [dict(n) for n in _G["nodes"]], "edges": _G["edges"]}}
    counts = apply_macro_temas(graphs, {"a::op:edital-x": ["agro - bioeconomia e alimentos"]})
    assert counts == {"a": 1}
    op = next(n for n in graphs["a"]["nodes"] if n["id"] == "op:edital-x")
    assert op["macro_temas"] == ["agro - bioeconomia e alimentos"]


def test_canonicalize_fresh_graph_replays_persisted_canon(monkeypatch):
    # O ingest replayia o canon persistido (kg_store `concept_canon`) de forma
    # determinística; llm_new=False não chama LLM nenhum.
    from core.kg import kg_store
    from core.kg.canonicalize import canonicalize_fresh_graph

    monkeypatch.setattr(kg_store, "load", lambda key, default=None: _CANON)
    g2 = canonicalize_fresh_graph(_G, file_key="finep__novo", llm_new=False)
    ids = {n["id"] for n in g2["nodes"]}
    assert "con:brasil" not in ids                      # descarte replayado
    assert "con:aprendizado-de-maquina" in ids          # merge replayado
    assert "con:ml" not in ids


def test_canonicalize_fresh_graph_without_canon_is_passthrough(monkeypatch):
    from core.kg import kg_store
    from core.kg.canonicalize import canonicalize_fresh_graph

    monkeypatch.setattr(kg_store, "load", lambda key, default=None: default)
    g2 = canonicalize_fresh_graph(_G, file_key="finep__novo", llm_new=False)
    assert g2 == _G


def test_build_canon_records_validated_ids():
    # `validated` = todos os ids julgados + os alvos canônicos dos merges —
    # o ingest usa isso p/ só validar Conceitos inéditos.
    assert set(_CANON["validated"]) == {
        "con:brasil", "con:bioinsumos", "con:ml", "con:aprendizado-de-maquina",
    }


# ── KG v2 resíduos PR-B: Frente 1 (descarte determinístico por classe errada) ──

def test_anti_class_verdict_flags_wrong_class():
    assert anti_class_verdict("TRL")["categoria"] == "metrica"
    assert anti_class_verdict("Technology Readiness Level")["categoria"] == "metrica"
    assert anti_class_verdict("nível de maturidade tecnológica")["categoria"] == "metrica"
    assert anti_class_verdict("LGPD")["categoria"] == "legal"
    assert anti_class_verdict("Lei nº 14.133")["categoria"] == "legal"
    assert anti_class_verdict("Marco Civil da Internet")["categoria"] == "legal"
    assert anti_class_verdict("Programa")["categoria"] == "generico"
    assert anti_class_verdict("tecnologia")["categoria"] == "generico"
    assert anti_class_verdict("consultoria")["categoria"] == "generico"
    # composto legítimo e tema real passam incólumes (→ julgamento LLM)
    for keep in ("tecnologia assistiva", "saúde digital", "inteligência artificial",
                 "eficiência energética", "internet das coisas"):
        assert anti_class_verdict(keep) is None


class _FakeCanonClient:
    """Cliente que devolve 'manter' p/ todo id recebido, gravando os ids vistos —
    p/ provar que os determinísticos NÃO chegam ao LLM."""

    def __init__(self):
        self.seen_ids: set[str] = set()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, messages, **_):
        payload = json.loads(messages[1]["content"])
        ids = [it["id"] for it in payload]
        self.seen_ids.update(ids)
        itens = [{"id": i, "veredicto": "manter"} for i in ids]
        msg = SimpleNamespace(content=json.dumps({"itens": itens}))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_propose_validation_deterministic_prefilter():
    concepts = {
        "con:trl": {"id": "con:trl", "name": "TRL", "dim": "tecnologia"},
        "con:lgpd": {"id": "con:lgpd", "name": "LGPD", "dim": "tema"},
        "con:programa": {"id": "con:programa", "name": "Programa", "dim": "tema"},
        "con:saude-digital": {"id": "con:saude-digital", "name": "saúde digital", "dim": "tema"},
        "con:tec-assistiva": {"id": "con:tec-assistiva", "name": "tecnologia assistiva",
                              "dim": "tecnologia"},
    }
    client = _FakeCanonClient()
    plan = propose_validation(concepts, client=client, model="fake")
    # determinísticos descartados SEM passar pelo LLM
    assert plan["con:trl"] == {"veredicto": "descartar", "categoria": "metrica"}
    assert plan["con:lgpd"] == {"veredicto": "descartar", "categoria": "legal"}
    assert plan["con:programa"] == {"veredicto": "descartar", "categoria": "generico"}
    assert client.seen_ids == {"con:saude-digital", "con:tec-assistiva"}
    # compostos legítimos seguem ao LLM e são mantidos
    assert plan["con:saude-digital"]["veredicto"] == "manter"
    assert plan["con:tec-assistiva"]["veredicto"] == "manter"


# ── KG v2 resíduos PR-B: Frente 2 (auto-merge da banda >0.90) ──────────────────

def test_variant_key_collapses_trivial_variants():
    assert _variant_key("rede elétrica") == _variant_key("redes elétricas")     # plural
    assert _variant_key("gestão de resíduo") == _variant_key("gestão para resíduo")  # conectivo
    assert _variant_key("energia solar") == _variant_key("solar energia")        # ordem
    assert _variant_key("Saúde Digital") == _variant_key("saude digital")        # caixa/acento
    # fix 2026-07-05: plural -ções, parentético de sigla, hífen/aglutinado
    assert _variant_key("tecnologia da informação e comunicações") == _variant_key(
        "tecnologias da informação e comunicação")
    assert _variant_key("tecnologia da informação e comunicação (TIC)") == _variant_key(
        "tecnologia da informação e comunicação")
    assert _variant_key("sistemas ciber-físicos") == _variant_key("sistemas ciberfísicos")
    # conceitos distintos NÃO colidem
    assert _variant_key("saúde digital") != _variant_key("saúde mental")
    assert _variant_key("produção animal") != _variant_key("produção vegetal")


def test_auto_variant_merges_only_high_conf_band():
    concepts = {
        "con:rede-eletrica": {"id": "con:rede-eletrica", "name": "rede elétrica",
                              "dim": "tema", "fan_in": 3},
        "con:redes-eletricas": {"id": "con:redes-eletricas", "name": "redes elétricas",
                                "dim": "tema", "fan_in": 1},
        "con:energia-solar": {"id": "con:energia-solar", "name": "energia solar",
                              "dim": "tema", "fan_in": 1},
    }
    members = list(concepts)
    pair_scores = {
        ("con:rede-eletrica", "con:redes-eletricas"): 0.97,   # variante na banda >0.90
        ("con:rede-eletrica", "con:energia-solar"): 0.88,
        ("con:redes-eletricas", "con:energia-solar"): 0.86,
    }
    auto, rest = _auto_variant_merges(members, concepts, pair_scores)
    assert len(auto) == 1
    g = auto[0]
    assert set(g["membros"]) == {"con:rede-eletrica", "con:redes-eletricas"}
    assert g["nome_canonico"] == "rede elétrica"   # maior fan_in vence
    assert g["auto"] is True
    assert rest == ["con:energia-solar"]            # não-variante fica p/ o LLM

    # mesma variante, mas fora da banda (cosseno < 0.90) → NÃO auto-merge
    low = {("con:rede-eletrica", "con:redes-eletricas"): 0.80}
    auto2, rest2 = _auto_variant_merges(
        ["con:rede-eletrica", "con:redes-eletricas"], concepts, low)
    assert auto2 == []
    assert set(rest2) == {"con:rede-eletrica", "con:redes-eletricas"}
