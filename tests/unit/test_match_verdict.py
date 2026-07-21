"""Testes do veredito LLM top-K (Estágio 3 do funil de match v3).

Input do juiz = ficha da linha de `entities` + matched_excerpts (Fase 2 do
v3-unified) — sem subgrafo. Cache por (workspace, oportunidade_id) com
invalidação por input_hash, inalterado.
"""
from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest

from core.services import match_verdict as mv

pytestmark = pytest.mark.unit

# ── fixtures ──────────────────────────────────────────────────────────────────

ROW = {
    "kind": "edital",
    "native_id": "finep:602",
    "name": "Chamada IA Embarcada",
    "description": "Subvenção para IA embarcada em defesa.",
    "mecanismo": "subvencao",
    "formato": "edital_periodico",
    "status": "aberta",
    "deadline": datetime.date(2026, 8, 1),
    "uf": None,
    "setores": ["Defesa", "TIC"],
    "tecnologias_tags": ["visão computacional", "ia embarcada"],
    "ticket_min": 500_000,
    "ticket_max": 2_000_000,
    "constraints": [
        {"tipo": "porte", "op": "in", "valor": ["mei", "me", "epp", "media"]},
        {"tipo": "parceria", "op": "exige", "valor": "ict"},
    ],
    "requisitos_texto": ["Plano de trabalho conforme Anexo I."],
    "metadata": {},
}

PROFILE = {"nome": "ACME", "tamanho_empresa": "ME", "uf": "SP", "one_liner": "visão p/ drones"}
EXCERPTS = [{
    "company_text": "Plataforma de visão computacional para drones autônomos.",
    "edital_text": "Sistemas de IA embarcada para plataformas não tripuladas.",
    "section": "Temas",
    "score": 0.91,
}]


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
    "red_flags_elegibilidade": ["Exige parceria com ICT."],
    "fit_mecanismo": "Subvenção casa com o estágio pré-receita.",
    "recomendacao": "alta",
}


# ── serialize_entity ─────────────────────────────────────────────────────────

def test_serialize_inclui_ficha_completa():
    s = mv.serialize_entity(ROW)
    assert "OPORTUNIDADE [edital]: Chamada IA Embarcada" in s
    assert "descrição: Subvenção para IA embarcada" in s
    assert "mecanismo: subvencao" in s
    assert "prazo: 01/08/2026" in s
    assert "setores: Defesa, TIC" in s
    assert "ticket: R$ 500.000 – R$ 2.000.000" in s
    assert "porte deve ser um de" in s          # constraint renderizada como frase
    assert "exige parceria com ict" in s
    assert "Plano de trabalho conforme Anexo I." in s


def test_serialize_investidor_usa_metadata():
    inv = {
        "kind": "investidor", "name": "KPTL", "description": "Tese deep-tech.",
        "setores": ["Multissetorial"], "ticket_min": None, "ticket_max": 5_000_000,
        "constraints": [], "requisitos_texto": [],
        "metadata": {"estagio_alvo": ["seed", "serie-a"], "lead_follow": "lead"},
    }
    s = mv.serialize_entity(inv)
    assert "OPORTUNIDADE [investidor]: KPTL" in s
    assert "estágio-alvo: seed, serie-a" in s
    assert "posição (lead/follow): lead" in s
    assert "ticket: até R$ 5.000.000" in s


# ── input hash ───────────────────────────────────────────────────────────────

def test_hash_estavel_e_sensivel_a_perfil_ficha_e_excerpts():
    s = mv.serialize_entity(ROW)
    h1 = mv.verdict_input_hash(s, PROFILE, EXCERPTS)
    assert h1 == mv.verdict_input_hash(s, dict(PROFILE), list(EXCERPTS))  # estável
    assert h1 != mv.verdict_input_hash(s, {**PROFILE, "uf": "SC"}, EXCERPTS)
    assert h1 != mv.verdict_input_hash(s + "\nnovo requisito", PROFILE, EXCERPTS)
    assert h1 != mv.verdict_input_hash(s, PROFILE, [])
    # ruído numérico de re-embedding (além de 3 casas) NÃO flapa o hash
    quase = [dict(EXCERPTS[0], score=0.9101)]
    outro = [dict(EXCERPTS[0], score=0.95)]
    assert h1 == mv.verdict_input_hash(s, PROFILE, quase)
    assert h1 != mv.verdict_input_hash(s, PROFILE, outro)


# ── compute_verdict ──────────────────────────────────────────────────────────

def test_compute_verdict_valida_e_envia_contexto():
    client = _FakeClient(VERDICT)
    out = mv.compute_verdict(mv.serialize_entity(ROW), PROFILE, EXCERPTS, client=client)
    assert out == VERDICT
    user_msg = client.calls[0]["messages"][1]["content"]
    assert "[PERFIL DA EMPRESA]" in user_msg
    assert "[TRECHOS QUE GERARAM O MATCH (Stage 2)]" in user_msg
    assert "[FICHA DA OPORTUNIDADE]" in user_msg
    assert "drones autônomos" in user_msg       # excerpt da empresa chegou ao juiz


def test_compute_verdict_output_invalido_vira_none():
    assert mv.compute_verdict("ficha", PROFILE, [], client=_FakeClient({"foo": 1})) is None
    ruim = dict(VERDICT, recomendacao="urgente")
    assert mv.compute_verdict("ficha", PROFILE, [], client=_FakeClient(ruim)) is None


def test_compute_verdict_erro_de_infra_fail_open():
    class _Boom:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**_):
                    raise RuntimeError("api down")

    assert mv.compute_verdict("ficha", PROFILE, [], client=_Boom()) is None


# ── verdict_key ──────────────────────────────────────────────────────────────

def test_verdict_key_por_kind():
    assert mv.verdict_key({"kind": "edital", "source": "finep", "edital_id": "602"}) == "finep__602"
    assert mv.verdict_key({"kind": "programa", "entity_id": "programa:centelha"}) == "programa:centelha"
    assert mv.verdict_key({"kind": "investidor", "entity_id": "investidor:kptl"}) == "investidor:kptl"
    assert mv.verdict_key({"kind": "edital"}) is None


# ── reorder ──────────────────────────────────────────────────────────────────

def test_reorder_alta_sobe_baixa_afunda_pendente_neutro():
    ms = [
        {"name": "b", "verdict": {"recomendacao": "baixa"}},
        {"name": "p", "verdict": None},
        {"name": "a", "verdict": {"recomendacao": "alta"}},
    ]
    assert [m["name"] for m in mv.reorder_by_verdict(ms)] == ["a", "p", "b"]


def test_reorder_sem_vereditos_preserva_ordem():
    ms = [{"name": str(i), "verdict": None} for i in range(4)]
    assert [m["name"] for m in mv.reorder_by_verdict(ms)] == ["0", "1", "2", "3"]


# ── cache ────────────────────────────────────────────────────────────────────

class _FakeDb:
    """Supabase-like: .table().select().eq().in_().execute().data."""

    def __init__(self, rows):
        self._rows = rows
        self.upserts: list[dict] = []

    def table(self, name):
        self._name = name
        return self

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def in_(self, *_):
        return self

    def upsert(self, row, **_):
        self.upserts.append(row)
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


def test_get_cached_verdicts_hash_velho_e_miss():
    rows = [
        {"oportunidade_id": "finep__602", "input_hash": "H1", "verdict": VERDICT},
        {"oportunidade_id": "finep__603", "input_hash": "VELHO", "verdict": VERDICT},
    ]
    hits = mv.get_cached_verdicts(_FakeDb(rows), "ws", {"finep__602": "H1", "finep__603": "H2"})
    assert "finep__602" in hits
    assert "finep__603" not in hits


def test_attach_cached_verdicts_anexa_e_devolve_misses(monkeypatch):
    monkeypatch.setattr(mv, "_entity_row", lambda oid: ROW if "602" in oid else None)
    match_dicts = [
        {"kind": "edital", "source": "finep", "edital_id": "602",
         "entity_id": "finep:602", "matched_excerpts": EXCERPTS},
        {"kind": "edital", "source": "finep", "edital_id": "999",
         "entity_id": "finep:999", "matched_excerpts": []},  # sumiu do corpus
    ]
    s = mv.serialize_entity(ROW)
    h = mv.verdict_input_hash(s, PROFILE, EXCERPTS)
    db = _FakeDb([{"oportunidade_id": "finep__602", "input_hash": h, "verdict": VERDICT}])
    misses = mv.attach_cached_verdicts(db, "ws", match_dicts, PROFILE)
    assert match_dicts[0]["verdict"] == VERDICT      # cache-hit anexado
    assert match_dicts[1]["verdict"] is None         # sem linha → sem veredito
    assert misses == []                              # hit não vira miss


def test_attach_cached_verdicts_miss_vira_item_de_task(monkeypatch):
    monkeypatch.setattr(mv, "_entity_row", lambda oid: ROW)
    match_dicts = [{"kind": "programa", "entity_id": "programa:centelha",
                    "matched_excerpts": EXCERPTS}]
    db = _FakeDb([])  # cache vazio
    misses = mv.attach_cached_verdicts(db, "ws", match_dicts, PROFILE)
    assert match_dicts[0]["verdict"] is None
    assert misses == [{"oportunidade_id": "programa:centelha", "excerpts": EXCERPTS}]


def test_serialize_for_verdict_resolve_linha(monkeypatch):
    monkeypatch.setattr(mv, "_entity_row", lambda oid: ROW if oid == "finep__602" else None)
    out = mv.serialize_for_verdict({"oportunidade_id": "finep__602", "excerpts": EXCERPTS})
    assert out is not None
    oid, s = out
    assert oid == "finep__602"
    assert "Chamada IA Embarcada" in s
    assert mv.serialize_for_verdict({"oportunidade_id": "finep__999"}) is None
