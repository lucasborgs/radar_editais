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
