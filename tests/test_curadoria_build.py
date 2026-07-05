"""Testes do build determinístico dos catálogos curados (KG v2, PR4.1)."""
from __future__ import annotations

from core.kg import curadoria_build as cb


def _by_type(g, type_, kind=None):
    return [n for n in g["nodes"]
            if n.get("type") == type_ and (kind is None or n.get("kind") == kind)]


def test_investidor_desdobramento_d2():
    """Cada fundo vira Ator(investidor) + Oportunidade(investimento) — o D2."""
    g = cb.build_investidores_graph()
    assert g is not None
    atores = _by_type(g, "Ator", "investidor")
    ofertas = _by_type(g, "Oportunidade", "investimento")
    assert atores and ofertas
    # 1:1 fundo↔oferta
    assert len(atores) == len(ofertas)
    off = ofertas[0]
    assert off["aperture"] == "continua"
    assert off["mecanismo"] == ["equity"]
    # facetas estruturadas preservadas (o que o LLM achatava)
    assert any(o.get("url") for o in ofertas)
    assert any(o.get("estagio_alvo") for o in ofertas)


def test_investidor_offer_belongs_to_actor():
    """A Oportunidade(investimento) aponta para o Ator via `pertence_a`."""
    g = cb.build_investidores_graph()
    op_ids = {n["id"] for n in _by_type(g, "Oportunidade", "investimento")}
    ator_ids = {n["id"] for n in _by_type(g, "Ator", "investidor")}
    pertence = [e for e in g["edges"] if e["type"] == "pertence_a"]
    assert pertence
    for e in pertence:
        assert op_ids & set(e["members"])
        assert ator_ids & set(e["members"])


def test_investidor_concepts_attributed_to_actor():
    """Conceitos ligam-se ao fundo por `viabiliza` — a atribuição concept→fundo no
    match (como as ICTs). Alguns Conceitos curados podem virar Ator na higiene
    canônica (retipagem), então a asserção é: TODA aresta inclui o fundo, e a
    MAIORIA liga a um Conceito (o caminho de atribuição existe)."""
    g = cb.build_investidores_graph()
    by_id = {n["id"]: n for n in g["nodes"]}
    inv_ids = {n["id"] for n in _by_type(g, "Ator", "investidor")}
    viab = [e for e in g["edges"] if e["type"] == "viabiliza"]
    assert viab
    to_concept = 0
    for e in viab:
        assert len(e["members"]) == 2
        assert all(m in by_id for m in e["members"])  # sem membros dangling
        assert inv_ids & set(e["members"])            # sempre inclui o fundo
        if any(by_id[m].get("type") == "Conceito" for m in e["members"]):
            to_concept += 1
    assert to_concept >= len(viab) - 3  # quase todas ligam a um Conceito


def test_programa_enriquecido_com_url_e_mecanismo():
    g = cb.build_programas_graph()
    assert g is not None
    progs = _by_type(g, "Oportunidade", "programa")
    assert progs
    assert all(p["aperture"] == "recorrente" for p in progs)
    assert any(p.get("url") for p in progs)
    assert any(p.get("mecanismo") for p in progs)  # tipo→slug canônico
    # elegibilidade curada vira requisitos_texto (insumo do produtor de constraints)
    assert any(p.get("requisitos_texto") for p in progs)


def test_macro_temas_dentro_do_vocab():
    """`macro_temas` só carrega valores do vocabulário controlado (D8)."""
    from core.kg.schema import macro_temas_vocab
    vocab = set(macro_temas_vocab())
    for builder in (cb.build_investidores_graph, cb.build_programas_graph):
        g = builder()
        for n in g["nodes"]:
            for m in n.get("macro_temas", []):
                assert m in vocab


def test_no_llm_needed():
    """O build é determinístico — roda sem OPENAI_API_KEY (sem chamada de LLM)."""
    import os
    key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        assert cb.build_investidores_graph() is not None
        assert cb.build_programas_graph() is not None
    finally:
        if key is not None:
            os.environ["OPENAI_API_KEY"] = key
