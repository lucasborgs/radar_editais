"""Radar unificado (Layer 2) — merge/normalização/cap. Puro, sem rede.

`merge_radar` funde listas de eventos (HybridMatch) e entidades (investor_match)
num ranking só. `_why_now_event` lê core.kg.temporal e degrada para "" com ids
fake — então os testes focam em tagging de quadrante, ordenação e cap.
"""
from __future__ import annotations

from core.radar_service import _apply_cap, merge_radar


def _ev(id_, score, otype="edital", title="E", eligible=True):
    return {"id": id_, "score": score, "opportunity_type": otype, "title": title,
            "eligible": eligible}


def _inv(id_, score, name="Fundo"):
    return {"id": id_, "score": score, "name": name}


def test_merge_tags_kind_class_and_type():
    out = merge_radar([_ev("finep:1", 8.0)], [_inv("investidor:kptl", 7.0)])
    by_id = {it["id"]: it for it in out["radar"]}
    assert by_id["finep:1"]["kind_class"] == "evento"
    assert by_id["finep:1"]["opportunity_type"] == "edital"
    assert by_id["investidor:kptl"]["kind_class"] == "entidade"
    assert by_id["investidor:kptl"]["opportunity_type"] == "investidor"
    assert by_id["investidor:kptl"]["title"] == "Fundo"          # name → title
    assert by_id["investidor:kptl"]["why_now"] == "Match de tese (sempre aberto)"


def test_merge_threads_verificacao():
    """`verificacao` flui do match de evento (badge provisorio na UI); entidade
    é diretório curado → sempre verificado; evento sem o campo → verificado."""
    ev_prov = {**_ev("web:abc", 8.0), "verificacao": "provisorio"}
    out = merge_radar([ev_prov, _ev("finep:1", 7.0)], [_inv("investidor:kptl", 7.0)])
    by_id = {it["id"]: it for it in out["radar"]}
    assert by_id["web:abc"]["verificacao"] == "provisorio"
    assert by_id["finep:1"]["verificacao"] == "verificado"
    assert by_id["investidor:kptl"]["verificacao"] == "verificado"


def test_rrf_interleaves_instead_of_blocking_by_raw_score():
    """RRF funde por POSIÇÃO, não nota crua: o melhor evento sobe junto do melhor
    fundo em vez de todos os fundos (score mais alto) virem em bloco."""
    events = [_ev("e1", 6.0), _ev("e2", 5.5), _ev("e3", 5.0)]
    entities = [_inv("i1", 9.0), _inv("i2", 8.0)]
    out = merge_radar(events, entities)
    # sem RRF (score cru) seria: i1, i2, e1, e2, e3 — entidades em bloco.
    # com RRF: rank1-vs-rank1 empata e intercala (desempate por score cru).
    assert [it["id"] for it in out["radar"]] == ["i1", "e1", "i2", "e2", "e3"]


def test_rrf_tiebreak_by_raw_score():
    """Mesmo rank nas duas fontes → empata no RRF → desempata pelo score cru."""
    out = merge_radar([_ev("e1", 5.0)], [_inv("i1", 9.0)])
    assert [it["id"] for it in out["radar"]] == ["i1", "e1"]


def test_single_source_rrf_preserves_score_order():
    """Fonte única: RRF = ordem de score (rank segue o score)."""
    out = merge_radar([_ev("a", 3.0), _ev("b", 9.0), _ev("c", 6.0)], [])
    assert [it["id"] for it in out["radar"]] == ["b", "c", "a"]


def test_merge_truncates_to_top_k():
    events = [_ev(f"e{i}", float(i)) for i in range(10)]
    out = merge_radar(events, [], top_k=3)
    assert len(out["radar"]) == 3
    assert [it["id"] for it in out["radar"]] == ["e9", "e8", "e7"]


def test_counts_by_opportunity_type():
    out = merge_radar(
        [_ev("e1", 8.0, "edital"), _ev("d1", 7.0, "desafio")],
        [_inv("i1", 6.0)],
    )
    assert out["counts"] == {"edital": 1, "desafio": 1, "investidor": 1}


def test_merge_handles_empty_lists():
    assert merge_radar([], [])["radar"] == []
    assert merge_radar([], [], top_k=5)["counts"] == {}


def test_apply_cap_limits_per_type_keeping_order():
    items = [
        {"id": "e1", "opportunity_type": "edital", "score": 9},
        {"id": "e2", "opportunity_type": "edital", "score": 8},
        {"id": "e3", "opportunity_type": "edital", "score": 7},
        {"id": "i1", "opportunity_type": "investidor", "score": 6},
    ]
    kept = _apply_cap(items, per_type_cap=2)
    assert [it["id"] for it in kept] == ["e1", "e2", "i1"]  # e3 cortado pelo cap


def test_apply_cap_none_is_noop():
    items = [{"id": "e1", "opportunity_type": "edital", "score": 9}]
    assert _apply_cap(items, None) == items


def test_floor_demotes_weak_entity_below_strong_event():
    """Fundo abaixo do floor (6.0) afunda abaixo de um evento forte, mesmo com
    score cru maior — senão o rank-1 fraco boiaria no topo."""
    out = merge_radar([_ev("e1", 5.0)], [_inv("i1", 5.5)])  # i1 < floor → fraco
    radar = out["radar"]
    assert [it["id"] for it in radar] == ["e1", "i1"]   # sem floor seria [i1, e1]
    tier = {it["id"]: it["tier"] for it in radar}
    assert tier == {"e1": "forte", "i1": "fraco"}


def test_floor_demotes_ineligible_event():
    """Evento aproximado (eligible=False) vai pro tier fraco, abaixo de entidade forte."""
    out = merge_radar([_ev("e1", 9.0, eligible=False)], [_inv("i1", 7.0)])
    assert [it["id"] for it in out["radar"]] == ["i1", "e1"]  # sem floor seria [e1, i1]
    tier = {it["id"]: it["tier"] for it in out["radar"]}
    assert tier["e1"] == "fraco" and tier["i1"] == "forte"


def test_floor_demotes_but_never_drops():
    """Match fraco continua presente (spec §3.8: entidade nunca some) — só rebaixado."""
    out = merge_radar([], [_inv("i1", 2.0)])  # único match, bem fraco
    assert [it["id"] for it in out["radar"]] == ["i1"]
    assert out["radar"][0]["tier"] == "fraco"


def test_cap_applied_anti_flood_in_merge():
    # 4 editais dominariam; cap=1 garante o investidor no ranking final.
    events = [_ev(f"e{i}", 9.0 - i) for i in range(4)]
    out = merge_radar(events, [_inv("i1", 5.0)], top_k=3, per_type_cap=1)
    ids = [it["id"] for it in out["radar"]]
    assert "i1" in ids
    assert sum(1 for it in out["radar"] if it["opportunity_type"] == "edital") == 1
