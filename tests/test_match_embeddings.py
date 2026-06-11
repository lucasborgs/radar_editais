"""Stage 2a por embeddings — text builders, cosseno, cache e roteamento da flag.

Sem rede: `embed_texts` é monkeypatchado com vetores determinísticos.
"""
from __future__ import annotations

from dataclasses import dataclass

import core.match_embeddings as me
from domain.user_profile import CompanyProfile


@dataclass
class _FakeStage1:
    edital_id: str
    card: dict


def _profile() -> CompanyProfile:
    return CompanyProfile(
        nome="ACME", tipo_entidade="empresa",
        one_liner="Plataforma de bioeconomia florestal.",
        solution_summary="Sensores IoT para manejo de florestas.",
        descricao_atividades="Monitoramento de carbono em áreas de reflorestamento.",
    )


# --- text builders -------------------------------------------------------

def test_edital_embedding_text_joins_title_objective_themes():
    card = {"title": "Edital Agro", "objective": "Fomentar bioeconomia",
            "themes": ["agro", "bioeconomia"]}
    txt = me.edital_embedding_text(card)
    assert "Edital Agro" in txt and "Fomentar bioeconomia" in txt
    assert "agro, bioeconomia" in txt


def test_edital_embedding_text_falls_back_to_eligible_sectors():
    card = {"title": "T", "eligible_sectors": ["software"]}
    assert "software" in me.edital_embedding_text(card)


def test_profile_embedding_text_is_the_pitch():
    txt = me.profile_embedding_text(_profile())
    assert "bioeconomia florestal" in txt
    assert "Sensores IoT" in txt


# --- cosseno -------------------------------------------------------------

def test_cosine_identical_is_one():
    assert me._cosine([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_orthogonal_is_zero():
    assert me._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_zero_vector():
    assert me._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# --- cache ---------------------------------------------------------------

def test_embed_with_cache_only_embeds_misses(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_CACHE_PATH", tmp_path / "cache.json")
    calls = {"n": 0}

    def fake_embed(texts):
        calls["n"] += 1
        return [[float(len(t)), 1.0] for t in texts]

    monkeypatch.setattr(me, "embed_texts", fake_embed)

    v1 = me.embed_with_cache(["aaa", "bb"])
    assert v1 == [[3.0, 1.0], [2.0, 1.0]]
    assert calls["n"] == 1
    # Segunda chamada: tudo em cache → sem nova chamada à API.
    v2 = me.embed_with_cache(["aaa", "bb"])
    assert v2 == v1
    assert calls["n"] == 1


# --- scorer --------------------------------------------------------------

def test_score_stage2a_embeddings_ranks_by_cosine(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_CACHE_PATH", tmp_path / "cache.json")

    # Vetores fabricados: edital "alinhado" colinear ao perfil; "ruído" ortogonal.
    fakes = {
        "PITCH": [1.0, 0.0],
        "ALIGN": [1.0, 0.0],
        "NOISE": [0.0, 1.0],
    }

    def fake_embed(texts):
        return [fakes[t] for t in texts]

    monkeypatch.setattr(me, "embed_texts", fake_embed)
    monkeypatch.setattr(me, "profile_embedding_text", lambda p: "PITCH")
    monkeypatch.setattr(me, "edital_embedding_text",
                        lambda card: "ALIGN" if card["id"] == "a" else "NOISE")

    eligible = [_FakeStage1("a", {"id": "a"}), _FakeStage1("b", {"id": "b"})]
    scores = me.score_stage2a_embeddings(eligible, _profile())
    assert scores["a"] == 10.0   # colinear → cos 1.0 → 10
    assert scores["b"] == 0.0    # ortogonal → cos 0.0 → 0


def test_score_stage2a_embeddings_empty_profile_returns_empty(monkeypatch):
    monkeypatch.setattr(me, "profile_embedding_text", lambda p: "")
    assert me.score_stage2a_embeddings([_FakeStage1("a", {"id": "a"})], _profile()) == {}


def test_score_stage2a_embeddings_degrades_on_error(monkeypatch):
    def boom(texts):
        raise RuntimeError("api down")
    monkeypatch.setattr(me, "embed_texts", boom)
    monkeypatch.setattr(me, "_CACHE_PATH", "/nonexistent/cache.json")
    out = me.score_stage2a_embeddings([_FakeStage1("a", {"id": "a"})], _profile())
    assert out == {}  # contrato: falha → {} (ranking degrada pro Stage 1)


# --- roteamento da flag --------------------------------------------------

def test_call_stage2_scores_routes_to_embeddings(monkeypatch):
    import core.services.hybrid_match_service as hms
    monkeypatch.setenv("MATCH_STAGE2A_BACKEND", "embeddings")
    monkeypatch.setattr(
        "core.match_embeddings.score_stage2a_embeddings",
        lambda eligible, profile: {"finep:X": 7.0},
    )
    out = hms._call_stage2_scores([_FakeStage1("finep:X", {"id": "finep:X"})], _profile())
    assert out == {"finep:X": 7.0}


def test_call_stage2_scores_default_is_llm(monkeypatch):
    import core.services.hybrid_match_service as hms
    monkeypatch.delenv("MATCH_STAGE2A_BACKEND", raising=False)
    called = {"embeddings": False}
    monkeypatch.setattr(
        "core.match_embeddings.score_stage2a_embeddings",
        lambda eligible, profile: called.__setitem__("embeddings", True) or {},
    )
    # Sem OPENAI_API_KEY o caminho LLM falha graciosamente → {}, mas NÃO chama embeddings.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    hms._call_stage2_scores([_FakeStage1("finep:X", {"id": "finep:X", "title": "T"})], _profile())
    assert called["embeddings"] is False
