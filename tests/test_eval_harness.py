"""Testes do harness de avaliação (core/eval/harness.py).

Exercita o fallback local de forma hermética (sem Langfuse, sem rede): suíte
dummy com task/evaluators triviais. Cobre round-trip de scores, agregação,
isolamento de falha de caso, run_evaluators, skip por prereqs e persistência.
"""
from __future__ import annotations

import json

import pytest

from core.eval.harness import Suite, get_input, run_suite


@pytest.fixture(autouse=True)
def _no_langfuse(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


def _square_suite(**kw) -> Suite:
    return Suite(
        name="dummy",
        description="quadrado",
        load_data=lambda: [
            {"input": 2, "expected_output": 4, "metadata": {"case_id": "a"}},
            {"input": 3, "expected_output": 9, "metadata": {"case_id": "b"}},
        ],
        task=lambda *, item, **_: get_input(item) ** 2,
        evaluators=[
            lambda *, output, expected_output, **_: {
                "name": "correct", "value": output == expected_output
            },
        ],
        **kw,
    )


def test_local_roundtrip_and_aggregate(tmp_path):
    res = run_suite(_square_suite(), out_dir=tmp_path)
    assert res["backend"] == "local"
    assert res["n_cases"] == 2
    assert res["aggregate"]["mean_correct"] == 1.0  # 2²=4, 3²=9 → ambos corretos

    # persistiu um JSON com o mesmo conteúdo
    saved = json.loads((tmp_path / f"{res['run_name'].split('-')[-1]}_dummy.json").read_text())
    assert saved["aggregate"]["mean_correct"] == 1.0


def test_task_failure_is_isolated(tmp_path):
    def boom(*, item, **_):
        raise ValueError("explodiu")

    suite = _square_suite()
    suite.task = boom
    res = run_suite(suite, out_dir=tmp_path)
    # falha de caso não derruba a rodada; vira output {"error": ...}
    assert res["n_cases"] == 2
    assert all("error" in ir["output"] for ir in res["item_results"])


def test_run_evaluator_feeds_aggregate(tmp_path):
    def n_items(item_results):
        return {"name": "n_items", "value": len(item_results)}

    res = run_suite(_square_suite(run_evaluators=[n_items]), out_dir=tmp_path)
    assert res["aggregate"]["n_items"] == 2


def test_prereqs_skip(tmp_path):
    suite = _square_suite(prereqs=lambda: "faltou credencial")
    res = run_suite(suite, out_dir=tmp_path)
    assert res["skipped"] == "faltou credencial"
    assert "aggregate" not in res


def test_limit(tmp_path):
    res = run_suite(_square_suite(), out_dir=tmp_path, limit=1)
    assert res["n_cases"] == 1


def test_langfuse_path_quando_keys_setadas(tmp_path, monkeypatch):
    """Com LANGFUSE_* setadas, roteia para run_experiment (mockado), captura o
    URL do run e dá flush. Protege o caminho de produção do eval-storage."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    calls: dict = {}

    class _FakeResult:
        item_results: list = []
        run_evaluations: list = []
        dataset_run_url = "https://cloud.langfuse.com/run/abc"

    class _FakeLangfuse:
        def run_experiment(self, **kw):
            calls.update(kw)
            return _FakeResult()

        def flush(self):
            calls["flushed"] = True

    import langfuse
    monkeypatch.setattr(langfuse, "Langfuse", lambda *a, **k: _FakeLangfuse())

    res = run_suite(_square_suite(), out_dir=tmp_path)
    assert res["backend"] == "langfuse"
    assert res["langfuse_url"] == "https://cloud.langfuse.com/run/abc"
    assert calls["name"] == "dummy"        # run_experiment chamado com a suíte
    assert calls.get("flushed") is True     # exportou antes de sair


# --- suítes reais registradas (sem rodar task/LLM) -------------------------

def test_suites_registered():
    from core.eval.registry import SUITES
    assert set(SUITES) == {
        "matching", "rag", "writing", "extraction",
        "investor_match", "opportunity_type", "triage",
    }


def test_rag_skips_without_supabase(tmp_path, monkeypatch):
    """rag deve pular limpo (não estourar) quando faltam creds de retrieval."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    from core.eval.registry import get_suite
    res = run_suite(get_suite("rag"), out_dir=tmp_path)
    assert res.get("skipped")


def test_writing_skips_without_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("EVAL_WORKSPACE_ID", raising=False)
    from core.eval.registry import get_suite
    res = run_suite(get_suite("writing"), out_dir=tmp_path)
    assert res.get("skipped")
