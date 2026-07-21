"""Testes do parser de constraints v3 (constraints_producer.produce_from_text).

Entry point ADITIVO da Fase 1 (estendido no PR-B): lê o TEXTO das seções de
elegibilidade do silver e devolve `(constraints, requisitos_texto, exclusoes,
publico_alvo)` no vocabulário §4.4 — call B única do gold. Rodam sem LLM real —
um client fake devolve o JSON; testamos validação (fora do vocab é descartado),
normalização (número/UF/porte/exige), as listas de display e o contrato fail-open.
"""
from __future__ import annotations

import json

import pytest

from radar.core.kg import constraints_producer as cp

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake client OpenAI-compatível (chat.completions.create → JSON fixo)
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, content: str):
        payload = content

        class _Completions:
            def create(self, **_kw):
                msg = type("M", (), {"content": payload})
                choice = type("C", (), {"message": msg})
                return type("R", (), {"choices": [choice]})

        self.chat = type("Chat", (), {"completions": _Completions()})


def _client(obj: dict) -> _FakeClient:
    return _FakeClient(json.dumps(obj, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Elegibilidade típica → constraints estruturadas
# ---------------------------------------------------------------------------
def test_constraints_validas_normalizadas():
    resp = {
        "constraints": [
            {"tipo": "faturamento", "op": "lte", "valor": "4800000"},
            {"tipo": "idade_empresa_meses", "op": "lte", "valor": 12},
            {"tipo": "parceria", "op": "exige", "valor": "ICT"},
            {"tipo": "porte", "op": "in", "valor": ["ME", "EPP"]},
            {"tipo": "sede_uf", "op": "in", "valor": "sc"},
            {"tipo": "vinculo_incubacao", "op": "exige", "valor": True},
        ],
        "requisitos_texto": ["Empresa criada há no máximo 12 meses", "Parceria obrigatória com ICT"],
    }
    cons, req, _exc, _pa = cp.produce_from_text("seções de elegibilidade...", client=_client(resp))
    by_tipo = {c["tipo"]: c for c in cons}

    assert by_tipo["faturamento"]["valor"] == 4800000          # string → int
    assert by_tipo["idade_empresa_meses"]["valor"] == 12
    assert by_tipo["parceria"]["valor"] == "ict"               # ator minúsculo
    assert by_tipo["porte"]["valor"] == ["me", "epp"]          # porte minúsculo
    assert by_tipo["sede_uf"]["valor"] == ["SC"]               # UF maiúscula, virou lista
    assert by_tipo["vinculo_incubacao"]["valor"] is True
    assert req == ["Empresa criada há no máximo 12 meses", "Parceria obrigatória com ICT"]


def test_exclusoes_e_publico_alvo_extraidos():
    """Call B devolve também as listas de display exclusoes/publico_alvo (PR-B):
    limpas, dedup e cortadas em ≤6."""
    resp = {
        "constraints": [],
        "requisitos_texto": [],
        "exclusoes": ["Vedada a participação de grandes empresas", "vedada a participação de grandes empresas", "Não elegível para pessoa física"],
        "publico_alvo": ["startups", "microempresas (ME)", ""],
    }
    cons, req, exc, pa = cp.produce_from_text("texto", client=_client(resp))
    assert cons == [] and req == []
    # dedup case-insensitive + drop de vazio
    assert exc == ["Vedada a participação de grandes empresas", "Não elegível para pessoa física"]
    assert pa == ["startups", "microempresas (ME)"]


def test_listas_display_default_vazias():
    """JSON sem as chaves novas → listas vazias (não KeyError)."""
    cons, req, exc, pa = cp.produce_from_text("texto", client=_client({"constraints": []}))
    assert (cons, req, exc, pa) == ([], [], [], [])


def test_tipo_fora_do_vocab_descartado():
    resp = {
        "constraints": [
            {"tipo": "tema", "op": "in", "valor": ["saude"]},       # não é elegibilidade
            {"tipo": "faturamento", "op": "lte", "valor": 16000000},
            {"tipo": "porte", "op": "banana", "valor": ["me"]},     # op inválido
            {"tipo": "trl", "op": "gte", "valor": None},            # valor vazio
        ],
        "requisitos_texto": [],
    }
    cons, req, _exc, _pa = cp.produce_from_text("texto", client=_client(resp))
    assert [c["tipo"] for c in cons] == ["faturamento"]
    assert req == []


def test_valor_fora_do_enum_categorico_descartado():
    # achado do bake-off Fase 1.5: LLM às vezes emite forma_juridica/porte fora do
    # enum fechado (ex. "fundacao"/"sociedade limitada" p/ forma_juridica, ou um
    # headcount numérico p/ porte) — isso vazava e virava UNSAT espúrio no avaliador.
    resp = {
        "constraints": [
            {"tipo": "forma_juridica", "op": "in", "valor": ["empresa", "fundacao"]},
            {"tipo": "porte", "op": "lte", "valor": ["250"]},
            {"tipo": "porte", "op": "in", "valor": ["me", "epp"]},
        ],
        "requisitos_texto": [],
    }
    cons, _req, _exc, _pa = cp.produce_from_text("texto", client=_client(resp))
    assert [c["tipo"] for c in cons] == ["porte"]
    assert cons[0]["valor"] == ["me", "epp"]


def test_sede_uf_fora_do_enum_de_27_ufs_descartado():
    # achado da auditoria externa do bake-off Fase 1.5: editais NACIONAIS (sem
    # exigência real de sede) faziam o LLM emitir sede_uf="BR"/"Brasil" — não é
    # sigla de UF, e virava UNSAT espúrio p/ toda empresa (eliminava editais
    # nacionais legítimos, ex. ia_digital_mg x finep:779).
    resp = {
        "constraints": [
            {"tipo": "sede_uf", "op": "in", "valor": ["BR"]},
            {"tipo": "faturamento", "op": "lte", "valor": 16000000},
        ],
        "requisitos_texto": [],
    }
    cons, _req, _exc, _pa = cp.produce_from_text("texto", client=_client(resp))
    assert [c["tipo"] for c in cons] == ["faturamento"]


def test_sede_uf_variantes_de_nacional_descartadas():
    for variante in ("Brasil", "nacional", "BRA", "todo o territorio nacional"):
        resp = {"constraints": [{"tipo": "sede_uf", "op": "in", "valor": [variante]}], "requisitos_texto": []}
        cons, _req, _exc, _pa = cp.produce_from_text("texto", client=_client(resp))
        assert cons == [], f"variante {variante!r} deveria ser descartada"


def test_sede_uf_valido_continua_emitido():
    resp = {"constraints": [{"tipo": "sede_uf", "op": "in", "valor": ["SC", "SP"]}], "requisitos_texto": []}
    cons, _req, _exc, _pa = cp.produce_from_text("texto", client=_client(resp))
    assert cons == [{"tipo": "sede_uf", "op": "in", "valor": ["SC", "SP"]}]


def test_texto_vazio_nao_chama_llm():
    # sem texto → ([], []) sem tocar no client (mesmo que o client explodisse)
    class _Boom:
        def __getattr__(self, _):  # qualquer acesso levanta
            raise AssertionError("não deveria chamar o LLM com texto vazio")

    assert cp.produce_from_text("", client=_Boom()) == ([], [], [], [])
    assert cp.produce_from_text("   ", client=_Boom()) == ([], [], [], [])


def test_fail_open_em_json_quebrado():
    # JSON inválido → fail-open ([], []), não levanta
    cons, req, _exc, _pa = cp.produce_from_text("texto", client=_FakeClient("isto não é json"))
    assert cons == []
    assert req == []


def test_requisitos_limitados_e_higienizados():
    resp = {
        "constraints": [],
        "requisitos_texto": ["  req 1  ", "", "req 2", 123, "req 3", "req 4", "req 5", "req 6", "req 7", "req 8", "req 9"],
    }
    _cons, req, _exc, _pa = cp.produce_from_text("texto", client=_client(resp))
    assert "req 1" in req and "" not in req and 123 not in req
    assert len(req) <= 8  # teto
