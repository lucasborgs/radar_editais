"""
Testes do item "degradação visível no ingest" (auditoria 2026-06-12):

  • structurer conta páginas falhadas e persiste {n_pages_total, n_pages_failed}
    no meta do silver; acima de _FAILED_PAGES_ALERT_RATIO alerta em
    pipeline_errors.
  • health_check.check_sources_freshness detecta fonte parada (bronze sem
    arquivo novo há mais de N dias).

Sem rede e sem DB: client LLM é fake, log_pipeline_error é capturado,
filesystem via tmp_path.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.structurer as structurer  # noqa: E402
from pipeline.health_check import check_sources_freshness  # noqa: E402

# =============================================================================
# Fake LLM client — falha em páginas contendo o marcador "FAIL_PAGE"
# =============================================================================

_BLOCK_JSON = json.dumps(
    [{"section_path": ["1. Objeto"], "kind": "paragraph", "text": "conteúdo ok"}],
    ensure_ascii=False,
)


class _FakeCompletions:
    def create(self, *, model, messages, temperature, max_tokens):
        if "FAIL_PAGE" in messages[0]["content"]:
            raise RuntimeError("timeout simulado")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=_BLOCK_JSON))]
        )


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def _doc(name: str, units: list[str]) -> dict:
    return {"doc_name": name, "units": units}


# =============================================================================
# _structure_single_doc / structure_document — contagem de páginas falhadas
# =============================================================================

def test_single_doc_conta_paginas_falhadas():
    blocks, n_total, n_failed = structurer._structure_single_doc(
        _FakeClient(), "fake-model",
        _doc("Edital.pdf", ["página um", "FAIL_PAGE aqui", "página três"]),
    )
    assert n_total == 3
    assert n_failed == 1
    # Páginas boas seguem produzindo blocos (doc parcial, não zerado).
    assert {b["page"] for b in blocks} == {1, 3}


def test_single_doc_sem_falhas():
    blocks, n_total, n_failed = structurer._structure_single_doc(
        _FakeClient(), "fake-model", _doc("Edital.pdf", ["a", "b"]),
    )
    assert (n_total, n_failed) == (2, 0)
    assert len(blocks) == 2


def test_structure_document_agrega_stats_entre_docs(monkeypatch):
    monkeypatch.setattr(structurer, "_make_client", lambda: (_FakeClient(), "fake"))
    blocks, stats = structurer.structure_document([
        _doc("Edital.pdf", ["ok", "FAIL_PAGE"]),
        _doc("FAQ.pdf", ["ok", "ok", "FAIL_PAGE"]),
    ])
    assert stats == {"n_pages_total": 5, "n_pages_failed": 2}
    assert len(blocks) == 3  # 3 páginas boas → 1 bloco cada


# =============================================================================
# build_or_load_structured_doc — stats no meta + alerta acima do threshold
# =============================================================================

def _run_build(monkeypatch, tmp_path, units: list[str]) -> tuple[list[dict], dict, list]:
    """Roda build_or_load com client fake e SILVER_DIR temporário; captura
    alertas enviados a log_pipeline_error. Retorna (blocks, meta, alertas)."""
    import core.pipeline_errors as pe

    monkeypatch.setattr(structurer, "_make_client", lambda: (_FakeClient(), "fake"))
    monkeypatch.setattr(structurer, "SILVER_DIR", tmp_path)
    alerts: list[dict] = []
    monkeypatch.setattr(
        pe, "log_pipeline_error",
        lambda **kw: alerts.append(kw),
    )

    blocks = structurer.build_or_load_structured_doc(
        "finep", "999", [_doc("Edital.pdf", units)], force=True,
    )
    meta = json.loads(
        (tmp_path / "structured_docs" / "finep" / "999.meta.json").read_text()
    )
    return blocks, meta, alerts


def test_meta_persiste_page_stats(monkeypatch, tmp_path):
    _, meta, _ = _run_build(monkeypatch, tmp_path, ["ok"] * 9 + ["FAIL_PAGE"])
    assert meta["n_pages_total"] == 10
    assert meta["n_pages_failed"] == 1


def test_alerta_acima_do_threshold(monkeypatch, tmp_path):
    """2/5 falhadas (40%) > 20% → persiste em pipeline_errors com contexto."""
    _, meta, alerts = _run_build(
        monkeypatch, tmp_path, ["ok", "FAIL_PAGE", "ok", "FAIL_PAGE", "ok"],
    )
    assert meta["n_pages_failed"] == 2
    assert len(alerts) == 1
    assert alerts[0]["source"] == "finep"
    assert alerts[0]["edital_id"] == "finep:999"
    assert alerts[0]["context"]["n_pages_failed"] == 2
    assert alerts[0]["error"].category == "parse_error"


def test_sem_alerta_abaixo_do_threshold(monkeypatch, tmp_path):
    """1/10 falhada (10%) < 20% → meta registra mas não alerta."""
    _, meta, alerts = _run_build(monkeypatch, tmp_path, ["ok"] * 9 + ["FAIL_PAGE"])
    assert meta["n_pages_failed"] == 1
    assert alerts == []


def test_sem_alerta_sem_falhas(monkeypatch, tmp_path):
    _, meta, alerts = _run_build(monkeypatch, tmp_path, ["ok", "ok"])
    assert meta["n_pages_failed"] == 0
    assert alerts == []


# =============================================================================
# check_sources_freshness — fonte parada
# =============================================================================

def _touch(path: Path, age_days: float) -> None:
    path.write_text("{}")
    ts = time.time() - age_days * 86400
    os.utime(path, (ts, ts))


def test_freshness_ok_stale_e_no_bronze(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "BRONZE_DIR", tmp_path)
    (tmp_path / "fresca_raw").mkdir()
    _touch(tmp_path / "fresca_raw" / "snap.json", age_days=0.5)
    (tmp_path / "parada_raw").mkdir()
    _touch(tmp_path / "parada_raw" / "snap.json", age_days=10)

    registry = {
        "fresca": {"bronze_dir": "fresca_raw"},
        "parada": {"bronze_dir": "parada_raw"},
        "inerte": {"bronze_dir": "inerte_raw"},  # dir não existe
    }
    by_source = {r["source"]: r for r in check_sources_freshness(registry=registry)}

    assert by_source["fresca"]["status"] == "OK"
    assert by_source["parada"]["status"] == "STALE"
    assert by_source["parada"]["age_days"] >= 9
    # Fonte que nunca produziu = estado de config, não regressão — não alerta.
    assert by_source["inerte"]["status"] == "NO_BRONZE"
    assert by_source["inerte"]["age_days"] is None


def test_freshness_usa_arquivo_mais_recente(monkeypatch, tmp_path):
    """Bronze com snapshot velho E novo → conta o novo (fonte viva)."""
    import config

    monkeypatch.setattr(config, "BRONZE_DIR", tmp_path)
    (tmp_path / "src_raw").mkdir()
    _touch(tmp_path / "src_raw" / "velho.json", age_days=30)
    _touch(tmp_path / "src_raw" / "novo.json", age_days=1)

    [r] = check_sources_freshness(registry={"src": {"bronze_dir": "src_raw"}})
    assert r["status"] == "OK"
