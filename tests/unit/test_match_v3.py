"""Motor de match v3 (Fase 2) — funil Stage 0-2 + trilha investidor. Puro:
snapshot e lado empresa injetados por monkeypatch (sem DB/rede/embeddings).

Cobre os invariantes do gate:
  Stage 0 — somente a projeção temporal canônica ``active`` passa.
  Stage 1 — unsat ELIMINA; unknown NUNCA elimina (flag no card)
  Stage 2 — sum-of-max por chunk da empresa (nunca max global), boost de
            setores, piso, excerpts (pares reais, dedup, top-3)
  Investidor — gates de metadata só eliminam quando os dois lados declaram
"""
from __future__ import annotations

import datetime

import numpy as np
import pytest

from radar.core.services import match_v3
from radar.core.services.company_chunks import ephemeral_company_chunks
from radar.core.services.match_v3 import (
    OpportunityMatch,
    _EntityChunks,
    _Snapshot,
    score_entity,
    stage0_alive,
)
from radar.core.services.temporal_read_model import TemporalReadModel
from radar.domain.data_quality import TemporalMode, ValidityState

pytestmark = pytest.mark.unit

TODAY = datetime.date(2026, 7, 10)


# ---------------------------------------------------------------------------
# Stage 0 — vivo
# ---------------------------------------------------------------------------

def _temporal(mode, state):
    return TemporalReadModel(
        temporal_mode=mode, validity_state=state, temporal_value=None,
        decision_source="source",
    )


def test_stage0_only_canonical_active_passes():
    assert stage0_alive(_temporal(TemporalMode.CONTINUOUS, ValidityState.ACTIVE)) is True
    assert stage0_alive(_temporal(TemporalMode.UNKNOWN, ValidityState.NEEDS_REVIEW)) is False
    assert stage0_alive(_temporal(TemporalMode.FIXED, ValidityState.CLOSED)) is False


def test_match_default_uses_sao_paulo_day_and_explicit_as_of_wins(fake_funnel, monkeypatch):
    """Simula a fronteira UTC já em 11/07 enquanto São Paulo ainda é 10/07."""
    fake_funnel.opportunities[0]["deadline"] = TODAY
    monkeypatch.setattr(match_v3, "_today_sao_paulo", lambda: TODAY)

    default_ids = {
        item.entity_id for item in match_v3.find_matching_opportunities(
            None, top_k=10, min_affinity=0.0, boost=False,
        )
    }
    explicit_ids = {
        item.entity_id for item in match_v3.find_matching_opportunities(
            None, as_of=TODAY + datetime.timedelta(days=1), top_k=10,
            min_affinity=0.0, boost=False,
        )
    }

    assert "finep:bom" in default_ids
    assert "finep:bom" not in explicit_ids


# ---------------------------------------------------------------------------
# Stage 2 — sum-of-max + excerpts (score_entity puro)
# ---------------------------------------------------------------------------

def _unit(v):
    a = np.asarray(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def test_score_entity_sum_of_max_nao_max_global():
    """Empresa com 2 chunks: A casa os DOIS moderadamente (0.8/0.8 → média 0.8);
    B tem um spike único (0.9) e nada no outro (0.0 → média 0.45). Max global
    escolheria B; sum-of-max escolhe A."""
    c1, c2 = _unit([1, 0, 0]), _unit([0, 1, 0])
    company = np.stack([c1, c2])

    a1 = _unit([1, 0.75, 0])   # ~0.8 com c1... construção aproximada
    ent_a = _EntityChunks(texts=["a1"], sections=[[]], emb=np.stack([_unit([1, 1, 0])]))
    ent_b = _EntityChunks(
        texts=["b1"], sections=[[]], emb=np.stack([_unit([0.9, 0, np.sqrt(1 - 0.81)])]),
    )
    del a1

    aff_a, best_a, _ = score_entity(company, ["c1", "c2"], ent_a, ["profile", "profile"])
    aff_b, best_b, _ = score_entity(company, ["c1", "c2"], ent_b, ["profile", "profile"])
    # max global: B (0.9) > A (0.707); sum-of-max (média): A (0.707) > B (0.45)
    assert best_b > best_a
    assert aff_a > aff_b


def test_score_entity_excerpts_sao_os_pares_do_score():
    company = np.stack([_unit([1, 0, 0]), _unit([0, 1, 0])])
    ent = _EntityChunks(
        texts=["sobre agro", "sobre saude"],
        sections=[["Temas", "Agro"], ["Temas", "Saúde"]],
        emb=np.stack([_unit([1, 0.1, 0]), _unit([0.1, 1, 0])]),
    )
    aff, best, excerpts = score_entity(company, ["empresa agro", "empresa saude"], ent, ["profile", "profile"])
    assert len(excerpts) == 2  # 1 melhor par por chunk da empresa, dedup por chunk-edital
    assert excerpts[0]["score"] >= excerpts[1]["score"]
    pares = {(x["company_text"], x["edital_text"]) for x in excerpts}
    assert ("empresa agro", "sobre agro") in pares
    assert ("empresa saude", "sobre saude") in pares
    assert excerpts[0]["section"] in ("Agro", "Saúde")
    assert 0.0 <= aff <= 1.0 and 0.0 <= best <= 1.0


def test_anonymous_thin_onboarding_hyde_is_never_exposed(monkeypatch):
    monkeypatch.setattr("radar.core.retrieval.embedder.embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr("radar.core.retrieval.hyde.generate_hyde_doc", lambda _query: "pseudo documento HyDE")
    profile = {"nome": "ACME", "one_liner": "software para saúde"}
    texts, embeddings, origins = ephemeral_company_chunks(profile, use_hyde=True)
    assert "hyde" in origins
    entity = _EntityChunks(texts=["edital"], sections=[[]], emb=np.array([[1.0, 0.0]], dtype=np.float32))
    _, _, excerpts = score_entity(embeddings, texts, entity, origins)
    assert excerpts
    assert all(e["origin"] != "hyde" for e in excerpts)
    assert all(e["company_text"] != "pseudo documento HyDE" for e in excerpts)


def test_origins_are_fail_closed_for_workspace_profile_library_and_hyde():
    company = np.stack([_unit([1, 0]), _unit([1, 0]), _unit([1, 0])])
    entity = _EntityChunks(texts=["edital"], sections=[[]], emb=np.array([[1.0, 0.0]], dtype=np.float32))
    _, _, excerpts = score_entity(company, ["perfil", "biblioteca", "pseudo"], entity,
                                  ["profile", "library_doc", "hyde"])
    assert {e["origin"] for e in excerpts} <= {"profile", "library_doc"}


def test_company_chunks_nao_sao_consumidos_por_trilha_desativada(monkeypatch):
    calls = []
    monkeypatch.setattr(match_v3, "ensure_company_chunks", lambda *args, **kwargs: None)
    monkeypatch.setattr(match_v3, "load_company_chunks", lambda *args, **kwargs: (
        calls.append(kwargs.get("include_origins")) or (["perfil"], np.array([[1.0, 0.0]]), ["profile"])
    ))
    prepared = match_v3.prepare_company_side({"nome": "ACME"}, workspace_id="workspace")
    assert calls == [True]
    # Trilha investidor desativada: a chamada não carrega lado-empresa nem devolve fundos.
    assert match_v3.find_matching_investors(
        {"nome": "ACME"}, workspace_id="workspace", prepared_company=prepared
    ) == []
    assert calls == [True]


def test_score_entity_excerpts_dedup_e_top3():
    """4 chunks da empresa casando todos no MESMO chunk do edital → 1 excerpt."""
    company = np.stack([_unit([1, 0.1 * i, 0]) for i in range(4)])
    ent = _EntityChunks(texts=["unico"], sections=[[]], emb=np.stack([_unit([1, 0, 0])]))
    _, _, excerpts = score_entity(company, [f"c{i}" for i in range(4)], ent, ["profile"] * 4)
    assert len(excerpts) == 1
    assert excerpts[0]["edital_text"] == "unico"


# ---------------------------------------------------------------------------
# Funil completo (snapshot fake)
# ---------------------------------------------------------------------------

def _opp(native_id, *, kind="edital", deadline=None, status=None, constraints=None,
         setores=None, name=None):
    return {
        "id": f"id-{native_id}", "kind": kind, "source": native_id.split(":")[0],
        "native_id": native_id, "name": name or native_id, "description": f"desc {native_id}",
        "status": status, "deadline": deadline, "uf": None,
        "setores": setores or [], "tecnologias_tags": [],
        "ticket_min": None, "ticket_max": None,
        "constraints": constraints or [], "requisitos_texto": [], "metadata": {},
    }


def _chunks_for(vec_rows, texts=None):
    emb = np.stack([_unit(v) for v in vec_rows])
    n = emb.shape[0]
    return _EntityChunks(
        texts=texts or [f"t{i}" for i in range(n)], sections=[[] for _ in range(n)], emb=emb,
    )


@pytest.fixture
def fake_funnel(monkeypatch):
    """Snapshot com 3 editais: `bom` (casa), `ruim` (não casa), `inelegivel`
    (casa mas porte unsat). Lado empresa = 1 chunk [1,0,0]."""
    fut = TODAY + datetime.timedelta(days=30)
    opps = [
        _opp("finep:bom", deadline=fut, setores=["Saúde"]),
        _opp("finep:ruim", deadline=fut),
        _opp("finep:inelegivel", deadline=fut,
             constraints=[{"tipo": "porte", "op": "in", "valor": ["mei", "me"]}]),
        _opp("finep:morto", deadline=TODAY - datetime.timedelta(days=1)),
        _opp("programa:centelha", kind="programa", deadline=None, status="ativa"),
    ]
    chunks = {
        "id-finep:bom": _chunks_for([[1, 0.15, 0]], ["texto do edital bom"]),
        "id-finep:ruim": _chunks_for([[0, 0.1, 1]], ["texto irrelevante"]),
        "id-finep:inelegivel": _chunks_for([[1, 0, 0.1]], ["texto casado"]),
        "id-finep:morto": _chunks_for([[1, 0, 0]], ["morto"]),
        "id-programa:centelha": _chunks_for([[0.9, 0.3, 0]], ["programa centelha"]),
    }
    snap = _Snapshot(probe=("fake",), opportunities=opps, chunks=chunks, investors=[])
    monkeypatch.setattr(match_v3, "_get_snapshot", lambda: snap)
    monkeypatch.setattr(
        match_v3, "_company_side",
        lambda profile, **kw: (["a empresa faz X"], np.stack([_unit([1, 0, 0])])),
    )
    return snap


def test_funil_rankeia_e_filtra(fake_funnel):
    profile = {"nome": "ACME", "tamanho_empresa": "GRANDE"}
    ms = match_v3.find_matching_opportunities(
        profile, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )
    ids = [m.entity_id for m in ms]
    assert "finep:bom" in ids
    assert "programa:centelha" in ids          # kind=programa entra no funil
    assert "finep:morto" not in ids            # Stage 0
    assert "finep:inelegivel" not in ids       # Stage 1: porte GRANDE ∉ [mei, me]
    # ranking por afinidade: 'bom' (cos ~0.99) acima de 'ruim' (cos ~0)
    assert ids.index("finep:bom") < ids.index("finep:ruim")
    assert all(isinstance(m, OpportunityMatch) for m in ms)


def test_funil_unknown_nao_elimina(fake_funnel):
    """Perfil SEM porte: a constraint de porte vira unknown → edital fica,
    com flag nao_verificada no payload."""
    ms = match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )
    by_id = {m.entity_id: m for m in ms}
    assert "finep:inelegivel" in by_id
    assert by_id["finep:inelegivel"].elegibilidade["status"] == "nao_verificada"
    assert by_id["finep:inelegivel"].to_dict()["elegibilidade"]["unknown"]


def test_funil_sem_perfil_estruturado_nao_filtra(fake_funnel):
    ms = match_v3.find_matching_opportunities(
        None, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )
    # sem perfil o Stage 1 não roda (elegibilidade None), mas o lado empresa
    # (mockado) ainda existe — todos os vivos aparecem
    assert {m.entity_id for m in ms} >= {"finep:bom", "finep:ruim", "finep:inelegivel"}
    assert all(m.elegibilidade is None for m in ms)


def test_funil_piso_e_topk(fake_funnel):
    ms = match_v3.find_matching_opportunities(
        None, as_of=TODAY, top_k=10, min_affinity=0.5, boost=False,
    )
    ids = {m.entity_id for m in ms}
    assert "finep:ruim" not in ids   # cos ~0.07 < 0.5
    assert "finep:bom" in ids
    ms1 = match_v3.find_matching_opportunities(
        None, as_of=TODAY, top_k=1, min_affinity=0.0, boost=False,
    )
    assert len(ms1) == 1


def test_funil_boost_setores(fake_funnel):
    """Perfil que menciona 'saúde' → interseção com setores=['Saúde'] do edital
    'bom' multiplica a afinidade por 1.1 (e só a dele)."""
    profile = {"nome": "ACME", "one_liner": "diagnóstico em saúde digital"}
    sem = match_v3.find_matching_opportunities(
        profile, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )
    com = match_v3.find_matching_opportunities(
        profile, as_of=TODAY, top_k=10, min_affinity=0.0, boost=True,
    )
    aff_sem = {m.entity_id: m.affinity for m in sem}
    aff_com = {m.entity_id: m.affinity for m in com}
    assert aff_com["finep:bom"] == pytest.approx(aff_sem["finep:bom"] * 1.1, rel=1e-5)
    assert aff_com["finep:ruim"] == pytest.approx(aff_sem["finep:ruim"], rel=1e-5)


def test_funil_structural_boost_false_off(fake_funnel):
    """structural_boost=False (default) não altera o funil — célula baseline."""
    base = {m.entity_id: m.affinity for m in match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )}
    com = {m.entity_id: m.affinity for m in match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
        structural_boost=False,
    )}
    assert com == base


def test_funil_structural_boost_similar_a_neighbors(fake_funnel, monkeypatch):
    """Boost estrutural multiplica a afinidade de vizinhos `similar_a` de um
    match forte (acima do piso de produção)."""
    from radar.core.kg.spike import match_boost

    class _Graph:
        probe = ("fake",)
        neighbors = {
            "edital:finep:bom": [("edital:finep:ruim", 0.9)],
            "edital:finep:ruim": [("edital:finep:bom", 0.9)],
        }

    monkeypatch.setattr(match_boost, "_get_similar", lambda: _Graph())
    sem = {m.entity_id: m.affinity for m in match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )}
    com = {m.entity_id: m.affinity for m in match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
        structural_boost=True,
    )}
    assert com["finep:ruim"] == pytest.approx(
        sem["finep:ruim"] * (1 + match_boost.STRUCTURAL_ALPHA * 0.9), rel=1e-5,
    )
    assert com["finep:bom"] == pytest.approx(sem["finep:bom"], rel=1e-5)


def test_funil_structural_boost_fail_open(fake_funnel, monkeypatch):
    """DB indisponível (None) → fator identidade, funil intacto."""
    from radar.core.kg.spike import match_boost

    monkeypatch.setattr(match_boost, "_get_similar", lambda: None)
    base = {m.entity_id: m.affinity for m in match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )}
    com = {m.entity_id: m.affinity for m in match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
        structural_boost=True,
    )}
    assert com == base


def test_funil_kinds_filtra_programa(fake_funnel):
    so_editais = match_v3.find_matching_opportunities(
        None, as_of=TODAY, top_k=10, min_affinity=0.0, kinds=frozenset({"edital"}),
    )
    assert all(m.kind == "edital" for m in so_editais)
    so_prog = match_v3.find_matching_opportunities(
        None, as_of=TODAY, top_k=10, min_affinity=0.0, kinds=frozenset({"programa"}),
    )
    assert [m.entity_id for m in so_prog] == ["programa:centelha"]


def test_payload_shape(fake_funnel):
    m = match_v3.find_matching_opportunities(
        None, as_of=TODAY, top_k=1, min_affinity=0.0,
    )[0].to_dict()
    for key in ("kind", "source", "edital_id", "entity_id", "name", "score",
                "affinity", "setores", "matched_excerpts", "status", "prazo",
                "valor", "elegibilidade"):
        assert key in m
    assert m["matched_excerpts"], "explicação do match (trechos) obrigatória"
    x = m["matched_excerpts"][0]
    assert set(x) >= {"company_text", "edital_text", "score"}


# ---------------------------------------------------------------------------
# Trilha investidor — DESATIVADA (spec product-scope-catalog-deactivation.md)
# ---------------------------------------------------------------------------

def test_investidor_trilha_desativada_devolve_vazio(monkeypatch):
    """A trilha investidor retorna vazio mesmo com fundos no snapshot: investidores
    privados estão fora do escopo ativo e nunca são recomendados."""
    snap = _Snapshot(
        probe=("fake",), opportunities=[], chunks={}, investors=[
            {
                "id": "id-x", "native_id": "investidor:casa", "name": "Fundo",
                "description": "tese", "status": "ativa", "setores": [],
                "ticket_min": None, "ticket_max": None, "verificado_em": "2026-06-09",
                "metadata": {"estagio_alvo": [], "generalista": False, "fund_status": "ativo"},
                "_emb": _unit([1, 0.1, 0]),
            },
        ],
    )
    monkeypatch.setattr(match_v3, "_get_snapshot", lambda: snap)
    ms = match_v3.find_matching_investors(
        {"nome": "ACME", "estagio": "seed", "one_liner": "saúde digital"}, top_k=10
    )
    assert ms == []


# ---------------------------------------------------------------------------
# Caminhos de inovação por domínio (spec product-pathways-domain-matching.md)
# ---------------------------------------------------------------------------

def test_funil_anota_caminho_por_dominio(fake_funnel):
    """Cada match carrega tipo/caminho/explicação — anotação aditiva, sem
    alterar ranking. Default dos editais do fixture = financiamento público."""
    ms = match_v3.find_matching_opportunities(
        {"nome": "ACME", "one_liner": "projeto de diagnóstico"},
        as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )
    by_id = {m.entity_id: m for m in ms}
    m = by_id["finep:bom"]
    assert m.tipo == "financiamento"
    d = m.to_dict()
    assert d["tipo"] == "financiamento"
    assert set(d["caminho"]) == {
        "tipo", "entidade", "objetivo", "requisitos",
        "canal_de_acesso", "evidencias", "status", "proximo_passo",
    }
    assert d["caminho"]["entidade"] == "finep:bom"
    assert d["explicacao"]["proximo_passo"]


def test_funil_caminho_intencao_sem_projeto(fake_funnel):
    """Aceite 3: intenção sem projeto → status 'possibilidade' e pendência de
    hipótese de escopo, sem declarar elegibilidade."""
    ms = match_v3.find_matching_opportunities(
        {"nome": "ACME"}, as_of=TODAY, top_k=10, min_affinity=0.0, boost=False,
    )
    m = next(x for x in ms if x.entity_id == "finep:bom")
    assert m.caminho["status"] == "possibilidade"
    assert any("Defina o projeto" in p for p in m.explicacao["pendentes"])
    assert not m.explicacao["confirmados"]  # nenhum fato de elegibilidade inventado


def test_find_ict_partners_exige_projeto(monkeypatch):
    """ICTs são capacidades/parceiros: exigem projeto definido e nunca entram
    no ranking (não viram OpportunityMatch)."""
    from radar.core.kg import entity_catalog

    monkeypatch.setattr(
        entity_catalog, "list_entity_catalog",
        lambda key, *, tema, limit: [
            {"id": "ict:labx", "name": "Lab X", "description": "competências em saúde",
             "themes": ["Saúde"], "type": "Ator"},
        ],
    )
    assert match_v3.find_ict_partners({"nome": "ACME"}) == []
    partners = match_v3.find_ict_partners(
        {"nome": "ACME", "one_liner": "diagnóstico em saúde"},
    )
    assert len(partners) == 1
    p = partners[0]
    assert p["kind"] == "ict"
    assert p["caminho"]["tipo"] == "ict"
    assert p["caminho"]["status"] == "possibilidade"
    assert p["explicacao"]["proximo_passo"]


def test_find_ict_partners_fail_open(monkeypatch):
    from radar.core.kg import entity_catalog

    def boom(*_a, **_k):
        raise RuntimeError("db fora")

    monkeypatch.setattr(entity_catalog, "list_entity_catalog", boom)
    assert match_v3.find_ict_partners(
        {"nome": "ACME", "one_liner": "diagnóstico em saúde"},
    ) == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_split_native():
    assert match_v3._split_native("finep:589") == ("finep", "589")
    assert match_v3._split_native("programa:centelha") == ("programa", "centelha")
    assert match_v3._split_native("semfonte") == ("", "semfonte")
