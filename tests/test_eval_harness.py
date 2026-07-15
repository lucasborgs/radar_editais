"""Testes do harness de avaliação (core/eval/harness.py).

Exercita o fallback local de forma hermética (sem Langfuse, sem rede): suíte
dummy com task/evaluators triviais. Cobre round-trip de scores, agregação,
isolamento de falha de caso, run_evaluators, skip por prereqs e persistência.
"""
from __future__ import annotations

import json

import pytest

from core.eval.harness import Criterion, Suite, get_input, run_suite


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
    assert res["status"] == "diagnostic"
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
    assert res["status"] == "error"
    assert len(res["manifest"]["execution"]["errors"]) == 2


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
    assert res["status"] == "skipped"
    assert res["manifest"]["suite"]["name"] == "dummy"


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

    res = run_suite(_square_suite(), out_dir=tmp_path, publish=True)
    assert res["backend"] == "langfuse"
    assert res["langfuse_url"] == "https://cloud.langfuse.com/run/abc"
    assert calls["name"] == "dummy"        # run_experiment chamado com a suíte
    assert calls.get("flushed") is True     # exportou antes de sair


def test_gate_passes_versioned_criterion(tmp_path):
    suite = _square_suite(
        classification="gate",
        criteria=[Criterion("mean_correct", "gte", 1.0)],
        expected_cases=2,
    )
    res = run_suite(suite, intent="gate", out_dir=tmp_path)
    assert res["status"] == "passed"
    assert res["criteria_results"][0]["passed"] is True
    assert res["manifest"]["dataset"]["loaded_cases"] == 2


def test_gate_fails_criterion(tmp_path):
    suite = _square_suite(
        classification="gate",
        criteria=[Criterion("mean_correct", "gte", 1.1)],
        expected_cases=2,
    )
    res = run_suite(suite, intent="gate", out_dir=tmp_path)
    assert res["status"] == "failed"


def test_gate_rejects_limit_and_non_gate_suite(tmp_path):
    limited = run_suite(
        _square_suite(classification="gate"), intent="gate", limit=1, out_dir=tmp_path,
    )
    candidate = run_suite(
        _square_suite(classification="candidate"), intent="gate", out_dir=tmp_path,
    )
    assert limited["status"] == "error"
    assert candidate["status"] == "error"


def test_publish_rejects_limited_run(tmp_path):
    res = run_suite(_square_suite(), publish=True, limit=1, out_dir=tmp_path)
    assert res["status"] == "error"
    assert "limitada" in res["error"]


def test_publish_requires_langfuse_credentials(tmp_path):
    res = run_suite(_square_suite(), publish=True, out_dir=tmp_path)
    assert res["status"] == "error"
    assert "LANGFUSE_PUBLIC_KEY" in res["error"]


def test_gate_errors_when_dataset_is_incomplete(tmp_path):
    suite = _square_suite(
        classification="gate",
        criteria=[Criterion("mean_correct", "gte", 1.0)],
        expected_cases=3,
    )
    res = run_suite(suite, intent="gate", out_dir=tmp_path)
    assert res["status"] == "error"
    assert any(e["stage"] == "completeness" for e in res["manifest"]["execution"]["errors"])


def test_manifest_rejects_secret_env_allowlist(tmp_path):
    with pytest.raises(ValueError, match="variável sensível"):
        run_suite(_square_suite(manifest_env=["OPENAI_API_KEY"]), out_dir=tmp_path)


def test_manifest_hashes_dataset_and_ignores_secret_values(tmp_path, monkeypatch):
    dataset = tmp_path / "golden.json"
    dataset.write_text('{"case": 1}')
    monkeypatch.setenv("OPENAI_MODEL", "model-safe")
    monkeypatch.setenv("OPENAI_API_KEY", "never-write-this")
    res = run_suite(_square_suite(dataset_paths=[dataset]), out_dir=tmp_path)
    manifest = res["manifest"]
    assert manifest["dataset"]["files"][0]["sha256"]
    serialized = json.dumps(manifest)
    assert "model-safe" in serialized
    assert "never-write-this" not in serialized


# --- suítes reais registradas (sem rodar task/LLM) -------------------------

def test_suites_registered():
    from core.eval.registry import SUITES
    assert set(SUITES) == {
        "matching", "rag", "writing", "extraction",
        "opportunity_type", "triage", "writing_v2",
        "profile_extractor", "reranker", "structurer",
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
