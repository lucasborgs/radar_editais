"""Testes do vocab lint (core/vocab_lint).

Cobre B1 (coleta determinística de evidência a partir de index/histórico/bronze)
e B3 (montagem do relatório a partir de uma proposta mockada). Sem LLM real —
`propose` recebe um client injetado / o relatório recebe a proposta direto.
"""
from __future__ import annotations

import json

from core import vocab_lint

_VOCAB = ["saúde e ciências da vida", "energia e transição sustentável"]


# =============================================================================
# B1 — coleta de evidência
# =============================================================================

def _patch_index(monkeypatch, vigente, historico):
    monkeypatch.setattr(
        vocab_lint.kg_store, "load_index",
        lambda *, historico=False, _v=vigente, _h=historico: {"editais": _h if historico else _v},
    )
    monkeypatch.setattr(vocab_lint.ws, "tema_vocab", lambda: list(_VOCAB))


def test_collect_variacoes_fora_do_vocab(monkeypatch):
    vigente = [
        {"id": "finep:1", "source": "finep", "themes_raw": ["Saúde e Ciências da Vida"]},
        {"id": "finep:2", "source": "finep", "themes_raw": ["Quântica e Fotônica"]},
        {"id": "fapesp:3", "source": "fapesp", "themes_raw": ["quântica e fotônica", "Energia e Transição Sustentável"]},
    ]
    historico = [
        {"id": "finep:4", "source": "finep", "themes_raw": ["Quântica e Fotônica"]},
    ]
    _patch_index(monkeypatch, vigente, historico)

    agg = vocab_lint.collect_variacoes(set(_VOCAB))
    # "saúde..." e "energia..." estão no vocab → não aparecem.
    assert "saúde e ciências da vida" not in agg
    assert "energia e transição sustentável" not in agg
    # "quântica e fotônica" fora do vocab: 3 ocorrências (finep:2, fapesp:3, finep:4).
    assert agg["quântica e fotônica"]["count"] == 3
    assert set(agg["quântica e fotônica"]["exemplos"]) == {"finep:2", "fapesp:3", "finep:4"}
    assert agg["quântica e fotônica"]["fontes"] == {"finep", "fapesp"}


def test_collect_variacoes_dedup_index_historico(monkeypatch):
    # Mesmo id em vigente e histórico → conta UMA vez (dedup por id).
    entry = {"id": "finep:9", "source": "finep", "themes_raw": ["Robótica Avançada"]}
    _patch_index(monkeypatch, [entry], [entry])
    agg = vocab_lint.collect_variacoes(set(_VOCAB))
    assert agg["robótica avançada"]["count"] == 1


def test_collect_tema_livre_from_bronze(monkeypatch, tmp_path):
    bronze = tmp_path / "web_raw"
    bronze.mkdir()
    (bronze / "web_discovery_1.json").write_text(json.dumps([
        {"url_hash": "aaa", "url": "http://x/a", "tema_livre": "Computação Quântica"},
        {"url_hash": "bbb", "url": "http://x/b", "tema_livre": "computação quântica; Robótica"},
        {"url_hash": "ccc", "url": "http://x/c", "tema_livre": ""},  # ignorado
        {"url_hash": "ddd", "url": "http://x/d"},  # sem campo, ignorado
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(vocab_lint, "_WEB_BRONZE_DIR", bronze)

    agg = vocab_lint.collect_tema_livre()
    assert agg["computação quântica"]["count"] == 2
    assert agg["computação quântica"]["exemplos"] == ["web:aaa", "web:bbb"]
    assert agg["robótica"]["count"] == 1


def test_collect_sem_tema(monkeypatch):
    vigente = [
        {"id": "finep:1", "title": "Com tema", "themes": ["saúde e ciências da vida"], "objective": "x"},
        {"id": "finep:2", "title": "Sem tema", "themes": [], "objective": "objetivo aqui"},
        {"id": "web:3", "title": "Web sem tema", "themes": [], "descricao": "desc fallback"},
    ]
    _patch_index(monkeypatch, vigente, [])
    out = vocab_lint.collect_sem_tema()
    ids = {o["id"] for o in out}
    assert ids == {"finep:2", "web:3"}
    by_id = {o["id"]: o for o in out}
    assert by_id["finep:2"]["descricao"] == "objetivo aqui"
    assert by_id["web:3"]["descricao"] == "desc fallback"


def test_collect_evidence_serializable_and_sorted(monkeypatch, tmp_path):
    vigente = [
        {"id": "finep:1", "source": "finep", "themes_raw": ["Tema A"], "themes": []},
        {"id": "finep:2", "source": "finep", "themes_raw": ["Tema A"], "themes": []},
        {"id": "finep:3", "source": "finep", "themes_raw": ["Tema B"], "themes": ["saúde e ciências da vida"]},
    ]
    _patch_index(monkeypatch, vigente, [])
    monkeypatch.setattr(vocab_lint, "_WEB_BRONZE_DIR", tmp_path / "empty")

    ev = vocab_lint.collect_evidence()
    # fontes serializadas como lista (JSON-safe).
    json.dumps(ev)
    # ordenado por count desc: "tema a" (2) antes de "tema b" (1).
    keys = list(ev["variacoes"].keys())
    assert keys[0] == "tema a"
    assert ev["variacoes"]["tema a"]["fontes"] == ["finep"]


def test_has_evidence():
    assert not vocab_lint.has_evidence({"variacoes": {}, "tema_livre": {}, "sem_tema": []})
    assert vocab_lint.has_evidence({"variacoes": {}, "tema_livre": {}, "sem_tema": [{"id": "x"}]})


# =============================================================================
# B2 — proposta (parsing defensivo, sem LLM real)
# =============================================================================

class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeClient:
    def __init__(self, content):
        self._content = content
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kwargs):  # noqa: D401 - mimics openai client
        return type("R", (), {"choices": [_FakeChoice(self._content)]})()


_EVIDENCE = {"vocab": _VOCAB, "variacoes": {}, "tema_livre": {}, "sem_tema": []}


def test_propose_parses_fenced_json():
    content = '```json\n{"novos_temas": [{"tema": "x", "n": 3}], "sinonimos": [], "sem_acao": false}\n```'
    out = vocab_lint.propose(_EVIDENCE, client=_FakeClient(content), model="fast")
    assert out["novos_temas"][0]["tema"] == "x"
    assert out["sem_acao"] is False


def test_propose_defensive_on_garbage():
    out = vocab_lint.propose(_EVIDENCE, client=_FakeClient("not json at all"), model="fast")
    assert out["sem_acao"] is True
    assert out["novos_temas"] == []


# =============================================================================
# B3 — relatório
# =============================================================================

def _evidence_with_data():
    return {
        "vocab": list(_VOCAB),
        "variacoes": {"quântica e fotônica": {"count": 3, "exemplos": ["finep:2", "fapesp:3", "finep:4"], "fontes": ["fapesp", "finep"]}},
        "tema_livre": {"computação quântica": {"count": 2, "exemplos": ["web:aaa"], "fontes": ["web"]}},
        "sem_tema": [{"id": "finep:5", "title": "Edital X", "descricao": "desc"}],
    }


def test_build_report_sem_evidencia():
    ev = {"vocab": _VOCAB, "variacoes": {}, "tema_livre": {}, "sem_tema": []}
    report = vocab_lint.build_report(ev, None)
    assert "Sem evidência" in report
    assert "## 2. Evidências" not in report


def test_build_report_with_proposal():
    ev = _evidence_with_data()
    proposal = {
        "novos_temas": [{"tema": "computação quântica", "evidencia": ["finep:2", "fapesp:3", "finep:4"],
                         "n": 3, "justificativa": "3 evidências independentes"}],
        "sinonimos": [{"variacao": "quântica e fotônica", "canonico": "saúde e ciências da vida",
                       "evidencia": ["finep:2"], "justificativa": "equivalência"}],
        "sem_acao": False,
        "notas": "ok",
    }
    report = vocab_lint.build_report(ev, proposal)
    # 1. sumário
    assert "## 1. Sumário executivo" in report
    assert "1 tema(s) novo(s), 1 sinônimo(s)" in report
    # 2. evidências (tabela)
    assert "quântica e fotônica" in report
    # 4. diff yaml: vocab antigo + novo marcado
    assert "tema_vocab:" in report
    assert "computação quântica" in report
    assert "NOVO (proposto)" in report
    # diff _SYNONYMS (lowercased)
    assert '"quântica e fotônica": "saúde e ciências da vida",' in report
    # 5. checklist com gates
    assert "test_wiki_schema_consistency" in report
    assert "core.eval matching" in report


def test_build_report_sem_acao():
    ev = _evidence_with_data()
    proposal = {"novos_temas": [], "sinonimos": [], "sem_acao": True, "notas": "nada cruzou a barra"}
    report = vocab_lint.build_report(ev, proposal)
    assert "SEM AÇÃO" in report
    # evidências ainda aparecem (foi coletada), mas diff fica vazio.
    assert "sem novos temas a adicionar" in report


def test_write_report_creates_dir(tmp_path):
    out = tmp_path / "reports"
    path = vocab_lint.write_report("# hello", out, ts="20260612_000000")
    assert path.exists()
    assert path.name == "vocab_lint_20260612_000000.md"
    assert path.read_text(encoding="utf-8") == "# hello"
