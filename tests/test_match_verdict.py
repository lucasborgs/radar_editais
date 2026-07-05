"""Testes do veredito LLM top-K (KG v2, PR7 / Estágio 2 do funil de match)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.services import match_verdict as mv

# ── fixtures ──────────────────────────────────────────────────────────────────

GRAPH = {
    "format_version": 2,
    "nodes": [
        {
            "id": "op:finep-602", "type": "Oportunidade", "kind": "edital",
            "aperture": "prazo", "name": "Chamada IA Embarcada",
            "description": "Subvenção para IA embarcada em defesa.",
            "prazo": "2026-08-01", "status": "ABERTA", "valor": "R$ 2M",
            "mecanismo": ["subvencao"], "macro_temas": ["defesa e soberania"],
            "constraints": [
                {"tipo": "porte", "op": "in", "valor": ["mei", "me", "epp", "media"]},
            ],
            "requisitos_texto": ["Plano de trabalho conforme Anexo I."],
            "exclusoes_texto": ["Vedado a órgãos públicos."],
        },
        {"id": "ator:senai-sp", "type": "Ator", "kind": "ict", "name": "SENAI/SP"},
        {"id": "con:visao-computacional", "type": "Conceito", "dim": "tecnologia",
         "name": "visão computacional"},
    ],
    "edges": [
        {"type": "exige", "members": ["op:finep-602", "ator:senai-sp"],
         "description": "parceria obrigatória com ICT"},
        {"type": "cobre", "members": ["op:finep-602", "con:visao-computacional"]},
    ],
}

PROFILE = {"nome": "ACME", "tamanho_empresa": "ME", "uf": "SP", "one_liner": "visão p/ drones"}
PATHS = [{"src": "visão computacional", "dst": "visão computacional", "score": 0.91}]


class _FakeClient:
    """Cliente OpenAI-like que devolve um JSON fixo (e grava o que recebeu)."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


VERDICT = {
    "racional_afinidade": "Visão computacional conecta direto com o foco do edital.",
    "red_flags_elegibilidade": ["Exige parceria com ICT (SENAI/SP)."],
    "fit_mecanismo": "Subvenção casa com o estágio pré-receita.",
    "recomendacao": "alta",
}


# ── serializador ──────────────────────────────────────────────────────────────

def test_serialize_inclui_propriedades_constraints_texto_e_arestas():
    node = mv.opportunity_node(GRAPH)
    assert node is not None
    s = mv.serialize_opportunity(GRAPH, node)
    assert "Chamada IA Embarcada" in s and "[edital/prazo]" in s
    assert "mecanismo: subvencao" in s and "macro-temas: defesa e soberania" in s
    assert "porte deve ser um de" in s                      # constraint como frase
    assert "Plano de trabalho" in s and "Vedado a órgãos públicos" in s
    assert "visão computacional" in s                       # conceitos cobertos
    # arestas com rótulo de membro (D12) — o insumo das red flags
    assert "exige: Chamada IA Embarcada (Oportunidade/edital), SENAI/SP (Ator/ict)" in s


def test_opportunity_node_none_em_catalogo():
    catalogo = {"nodes": [{"id": "ator:x", "type": "Ator", "kind": "ict", "name": "X"}]}
    assert mv.opportunity_node(catalogo) is None


# ── input_hash (invalidação implícita) ────────────────────────────────────────

def test_hash_estavel_e_sensivel_a_perfil_oportunidade_e_paths():
    node = mv.opportunity_node(GRAPH)
    s = mv.serialize_opportunity(GRAPH, node)
    h = mv.verdict_input_hash(s, PROFILE, PATHS)
    assert h == mv.verdict_input_hash(s, dict(PROFILE), list(PATHS))  # determinístico
    assert h != mv.verdict_input_hash(s, {**PROFILE, "uf": "SC"}, PATHS)      # perfil mudou
    assert h != mv.verdict_input_hash(s + "\nnovo requisito", PROFILE, PATHS)  # opp mudou
    assert h != mv.verdict_input_hash(s, PROFILE, [])                          # paths mudaram
    # campos vazios do perfil não entram no hash (perfil {a: None} ≡ {})
    assert mv.verdict_input_hash(s, {"uf": None}, PATHS) == mv.verdict_input_hash(s, {}, PATHS)


# ── compute_verdict (validação + fail-open) ───────────────────────────────────

def test_compute_verdict_valida_e_envia_contexto():
    node = mv.opportunity_node(GRAPH)
    s = mv.serialize_opportunity(GRAPH, node)
    client = _FakeClient(VERDICT)
    v = mv.compute_verdict(s, PROFILE, PATHS, client=client, model="fake")
    assert v == VERDICT
    user = client.calls[0]["messages"][1]["content"]
    assert "PERFIL DA EMPRESA" in user and "ACME" in user
    assert "PARES DE AFINIDADE" in user and "0.91" in user
    assert "SUBGRAFO DA OPORTUNIDADE" in user


def test_compute_verdict_output_invalido_vira_none():
    node = mv.opportunity_node(GRAPH)
    s = mv.serialize_opportunity(GRAPH, node)
    # recomendação fora do enum → descarta (fail-open, sem raise)
    assert mv.compute_verdict(s, PROFILE, client=_FakeClient({**VERDICT, "recomendacao": "x"})) is None
    assert mv.compute_verdict(s, PROFILE, client=_FakeClient({})) is None
    # red flags fora do shape são coagidas, não derrubam
    v = mv.compute_verdict(s, PROFILE, client=_FakeClient({**VERDICT, "red_flags_elegibilidade": "não é lista"}))
    assert v is not None and v["red_flags_elegibilidade"] == []


def test_compute_verdict_erro_de_infra_fail_open():
    def _boom(**_):
        raise RuntimeError("api down")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_boom))
    )
    assert mv.compute_verdict("s", PROFILE, client=client) is None


# ── reordenação dentro do top-K ───────────────────────────────────────────────

def test_reorder_alta_sobe_baixa_afunda_pendente_neutro():
    ms = [
        {"edital_id": "1", "affinity": 3.0, "verdict": {"recomendacao": "baixa"}},
        {"edital_id": "2", "affinity": 2.5, "verdict": None},                       # pendente
        {"edital_id": "3", "affinity": 2.0, "verdict": {"recomendacao": "alta"}},
        {"edital_id": "4", "affinity": 1.5, "verdict": {"recomendacao": "media"}},
    ]
    out = [m["edital_id"] for m in mv.reorder_by_verdict(ms)]
    # alta primeiro; pendente (neutro) e media empatam preservando ordem de affinity
    assert out == ["3", "2", "4", "1"]


def test_reorder_sem_vereditos_preserva_ordem():
    ms = [{"edital_id": str(i), "verdict": None} for i in range(4)]
    assert [m["edital_id"] for m in mv.reorder_by_verdict(ms)] == ["0", "1", "2", "3"]


# ── cache helpers (db fake) ───────────────────────────────────────────────────

class _FakeDb:
    """Supabase-like mínimo para match_verdicts (select por workspace + in_)."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.upserts: list[dict] = []

    def table(self, name):
        assert name == "match_verdicts"
        return self

    def select(self, *_):
        self._mode = "select"
        return self

    def eq(self, *_):
        return self

    def in_(self, _col, ids):
        self._ids = set(ids)
        return self

    def upsert(self, row, **_):
        self._mode = "upsert"
        self.upserts.append(row)
        return self

    def execute(self):
        if self._mode == "upsert":
            return SimpleNamespace(data=None)
        return SimpleNamespace(data=[r for r in self.rows if r["oportunidade_id"] in self._ids])


def test_get_cached_verdicts_hash_velho_e_miss():
    rows = [
        {"oportunidade_id": "finep__602", "input_hash": "h1", "verdict": VERDICT},
        {"oportunidade_id": "finep__734", "input_hash": "STALE", "verdict": VERDICT},
    ]
    hits = mv.get_cached_verdicts(
        _FakeDb(rows), "ws", {"finep__602": "h1", "finep__734": "h2", "finep__999": "h3"}
    )
    assert set(hits) == {"finep__602"}  # hash velho e linha ausente são miss


def test_attach_cached_verdicts_anexa_e_devolve_misses(monkeypatch):
    from core.kg import kg_store

    monkeypatch.setattr(kg_store, "load_all_hypergraphs", lambda: {"finep__602": GRAPH})
    node = mv.opportunity_node(GRAPH)
    s = mv.serialize_opportunity(GRAPH, node)

    m_hit = {"source": "finep", "edital_id": "602", "paths": PATHS, "affinity": 2.0}
    m_off = {"source": "finep", "edital_id": "999", "paths": [], "affinity": 1.0}  # sem grafo
    h = mv.verdict_input_hash(s, PROFILE, PATHS)
    db = _FakeDb([{"oportunidade_id": "finep__602", "input_hash": h, "verdict": VERDICT}])

    misses = mv.attach_cached_verdicts(db, "ws", [m_hit, m_off], PROFILE)
    assert m_hit["verdict"] == VERDICT      # cache-hit anexado
    assert m_off["verdict"] is None         # sem subgrafo → sem veredito, sem miss
    assert misses == []

    # segundo cenário: cache vazio → o par vira miss p/ a task
    db2 = _FakeDb([])
    misses2 = mv.attach_cached_verdicts(db2, "ws", [dict(m_hit)], PROFILE)
    assert misses2 == [{"file_key": "finep__602", "paths": PATHS}]


# ── ofertas de investimento (PR8.1) ───────────────────────────────────────────

INVEST_CATALOG = {
    "format_version": 2,
    "proveniencia": {"fonte": "curadoria"},
    "nodes": [
        {"id": "op:acme-ventures-investimento", "type": "Oportunidade", "kind": "investimento",
         "aperture": "continua", "name": "Investimento — ACME Ventures",
         "description": "VC de deep-tech B2B.", "mecanismo": ["equity"],
         "macro_temas": ["tecnologias digitais e conectividade"],
         "estagio_alvo": ["seed", "serie-a"], "lead_follow": "lead",
         "ticket_range": {"min_brl": 500000, "max_brl": 2000000}, "url": "https://acme.vc"},
        {"id": "ator:acme-ventures", "type": "Ator", "kind": "investidor", "name": "ACME Ventures"},
        {"id": "con:deep-tech", "type": "Conceito", "dim": "tecnologia", "name": "deep-tech"},
        {"id": "con:b2b", "type": "Conceito", "dim": "aplicacao", "name": "b2b"},
        # fundo IRMÃO no mesmo arquivo — NÃO deve vazar no sub-grafo da ACME
        {"id": "op:outro-fundo-investimento", "type": "Oportunidade", "kind": "investimento",
         "name": "Investimento — Outro", "mecanismo": ["equity"]},
        {"id": "ator:outro-fundo", "type": "Ator", "kind": "investidor", "name": "Outro Fundo"},
        {"id": "con:fintech", "type": "Conceito", "dim": "aplicacao", "name": "fintech"},
    ],
    "edges": [
        {"type": "pertence_a", "members": ["op:acme-ventures-investimento", "ator:acme-ventures"]},
        {"type": "viabiliza", "members": ["ator:acme-ventures", "con:deep-tech"]},
        {"type": "viabiliza", "members": ["ator:acme-ventures", "con:b2b"]},
        {"type": "pertence_a", "members": ["op:outro-fundo-investimento", "ator:outro-fundo"]},
        {"type": "viabiliza", "members": ["ator:outro-fundo", "con:fintech"]},
    ],
}


PROGRAMA_CATALOG = {
    "format_version": 2,
    "proveniencia": {"fonte": "curadoria"},
    "nodes": [
        {"id": "op:centelha", "type": "Oportunidade", "kind": "programa",
         "aperture": "recorrente", "name": "Centelha",
         "description": "Fomento a ideias inovadoras.", "mecanismo": ["subvencao"],
         "macro_temas": ["tecnologias digitais e conectividade"]},
        {"id": "con:empreendedorismo", "type": "Conceito", "dim": "aplicacao",
         "name": "empreendedorismo"},
        # programa IRMÃO no mesmo arquivo — NÃO deve vazar no sub-grafo do Centelha
        {"id": "op:outro-programa", "type": "Oportunidade", "kind": "programa",
         "name": "Outro Programa", "mecanismo": ["subvencao"]},
        {"id": "con:saude", "type": "Conceito", "dim": "tema", "name": "saúde"},
    ],
    "edges": [
        {"type": "cobre", "members": ["op:centelha", "con:empreendedorismo"]},
        {"type": "cobre", "members": ["op:outro-programa", "con:saude"]},
    ],
}


def test_programa_subgraph_isola_programa_e_serializa():
    graphs = {"programas": PROGRAMA_CATALOG}
    sub = mv.programa_subgraph(graphs, "programa:centelha")  # entity_id
    assert sub is not None
    mini, node = sub
    assert node["kind"] == "programa"
    # sub-grafo = programa + o conceito ligado a ele; o OUTRO programa fica de fora
    assert {n["id"] for n in mini["nodes"]} == {"op:centelha", "con:empreendedorismo"}
    s = mv.serialize_opportunity(mini, node)
    assert "[programa/recorrente]" in s and "mecanismo: subvencao" in s
    assert "empreendedorismo" in s and "saúde" not in s      # sem vazamento
    # id inexistente / kind errado → None (fail-safe)
    assert mv.programa_subgraph(graphs, "programa:none") is None
    assert mv.programa_subgraph({"investidores": INVEST_CATALOG}, "investidor:acme-ventures") is None


def test_investment_offer_subgraph_isola_oferta_e_serializa_facetas():
    graphs = {"investidores": INVEST_CATALOG}
    sub = mv.investment_offer_subgraph(graphs, "investidor:acme-ventures")  # entity_id
    assert sub is not None
    mini, offer = sub
    assert offer["kind"] == "investimento"
    # sub-grafo = oferta + fundo + os 2 conceitos do fundo; o OUTRO fundo fica de fora
    assert {n["id"] for n in mini["nodes"]} == {
        "op:acme-ventures-investimento", "ator:acme-ventures", "con:deep-tech", "con:b2b",
    }
    s = mv.serialize_opportunity(mini, offer)
    assert "[investimento/continua]" in s and "mecanismo: equity" in s
    assert "estágio-alvo: seed, serie-a" in s and "posição (lead/follow): lead" in s
    assert "ticket: R$ 500.000 – R$ 2.000.000" in s
    assert "deep-tech" in s and "b2b" in s and "fintech" not in s     # sem vazamento
    assert "pertence_a" in s and "viabiliza" in s


def test_serialize_for_verdict_dispatch_edital_investimento_e_programa():
    graphs = {
        "investidores": INVEST_CATALOG, "programas": PROGRAMA_CATALOG, "finep__602": GRAPH,
    }
    ed = mv.serialize_for_verdict({"file_key": "finep__602", "paths": []}, graphs)
    inv = mv.serialize_for_verdict(
        {"kind": "investimento", "oportunidade_id": "investidor:acme-ventures", "paths": []}, graphs)
    prog = mv.serialize_for_verdict(
        {"kind": "programa", "oportunidade_id": "programa:centelha", "paths": []}, graphs)
    assert ed[0] == "finep__602" and "[edital/prazo]" in ed[1]
    assert inv[0] == "investidor:acme-ventures" and "[investimento/continua]" in inv[1]
    assert prog[0] == "programa:centelha" and "[programa/recorrente]" in prog[1]
    # id inexistente → None (fail-safe, o item é pulado na task)
    assert mv.serialize_for_verdict(
        {"kind": "investimento", "oportunidade_id": "investidor:none"}, graphs) is None
    assert mv.serialize_for_verdict(
        {"kind": "programa", "oportunidade_id": "programa:none"}, graphs) is None


def test_attach_cached_verdicts_entities_investidor_e_programa(monkeypatch):
    from core.kg import kg_store

    graphs = {"investidores": INVEST_CATALOG, "programas": PROGRAMA_CATALOG}
    monkeypatch.setattr(kg_store, "load_all_hypergraphs", lambda: graphs)
    inv_sub = mv.investment_offer_subgraph(graphs, "investidor:acme-ventures")
    inv_h = mv.verdict_input_hash(mv.serialize_opportunity(inv_sub[0], inv_sub[1]), PROFILE, [])
    prog_sub = mv.programa_subgraph(graphs, "programa:centelha")
    prog_h = mv.verdict_input_hash(mv.serialize_opportunity(prog_sub[0], prog_sub[1]), PROFILE, [])

    inv = {"kind": "investidor", "name": "ACME Ventures", "entity_id": "investidor:acme-ventures", "paths": []}
    prog = {"kind": "programa", "name": "Centelha", "entity_id": "programa:centelha", "paths": []}
    ict = {"kind": "ict", "name": "SENAI", "paths": []}                                # fora do escopo
    db = _FakeDb([
        {"oportunidade_id": "investidor:acme-ventures", "input_hash": inv_h, "verdict": VERDICT},
        {"oportunidade_id": "programa:centelha", "input_hash": prog_h, "verdict": VERDICT},
    ])

    misses = mv.attach_cached_verdicts_entities(db, "ws", [inv, prog, ict], PROFILE)
    assert inv["verdict"] == VERDICT and prog["verdict"] == VERDICT   # ambos anexados do cache
    assert ict["verdict"] is None                                    # ict sem veredito
    assert misses == []

    # cache vazio → miss keyed por entity_id, com o discriminador de kind correto
    db2 = _FakeDb([])
    misses2 = mv.attach_cached_verdicts_entities(db2, "ws", [dict(inv), dict(prog)], PROFILE)
    assert {"kind": "investimento", "oportunidade_id": "investidor:acme-ventures", "paths": []} in misses2
    assert {"kind": "programa", "oportunidade_id": "programa:centelha", "paths": []} in misses2
    assert len(misses2) == 2
