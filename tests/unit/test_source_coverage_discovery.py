"""Testes da instrumentação multicanal da Descoberta (RT03-T04).

Cobre:
  - atribuição de open_search (com família), DOU (sem família) e hub_expansion
  - família presente ou nula
  - domínio sem path/query
  - dedup entre canais
  - credencial ausente e fim de semana
  - resultado vazio
  - falha de query, triagem e extração
  - telemetria indisponível
  - retorno público, ledger e staging preservados
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import radar.core.ingestion.dou_feeder as dou_feeder
import radar.core.ingestion.opportunity_discovery as od
from radar.core.services import source_runs
from radar.core.web_search import SearchHit

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake in-memory store (ledger + index)
# ---------------------------------------------------------------------------

class _FakeStore:
    def __init__(self):
        self.ledger: dict = {"urls": [], "rejected": {}}

    def load(self, key, default=None):
        if key == "discovery_ledger":
            return self.ledger
        if key == "index":
            return {"editais": []}
        return {} if default is None else default

    def load_index(self, *, historico=False):
        return {"editais": []}

    def save(self, key, blob):
        if key == "discovery_ledger":
            self.ledger = blob


# ---------------------------------------------------------------------------
# Hit helpers
# ---------------------------------------------------------------------------

_HIT_A = SearchHit(title="Edital Inovação", url="https://fap.sc.gov.br/edital",
                   snippet="chamada de inovação", content="texto")
_HIT_B = SearchHit(title="Desafio Corp", url="https://empresa.com.br/desafio",
                   snippet="desafio de inovação", content="texto")
_HIT_D = SearchHit(title="DOU Oportunidade", url="https://dou.gov.br/oportunidade",
                   snippet="fomento público", content="texto", agency="MCTI",
                   full_text=True)
_HUB_HIT = SearchHit(title="Portal Inovação", url="https://hub.com.br/desafios",
                     snippet="hub de inovação", content="texto")
_CHILD_HIT = SearchHit(title="Desafio Filho", url="https://hub.com.br/desafio/1",
                       snippet="desafio", content="texto")

_APPROVE = {"is_opportunity": True, "is_hub": False, "agency": "", "reason": ""}
_HUB_PARENT = {"is_opportunity": False, "is_hub": True, "agency": "", "reason": "portal"}
_REJECT = {"is_opportunity": False, "is_hub": False, "agency": "", "reason": "irrelevante"}


def _make_record(url: str, title: str = "Oportunidade") -> dict:
    return {
        "url": od.normalize_web_url(url),
        "url_hash": od.web_url_hash(url),
        "title": title,
        "texto_cru": "texto longo " * 200,
        "prazo_envio": "31/12/2026",
        "publico_alvo": "empresas",
        "descricao": "oportunidade de fomento",
        "status": "ABERTA",
        "tema": "inovação",
        "tema_livre": "",
        "opportunity_type": "edital",
        "agency": "",
        "fonte": "Web (descoberta)",
        "verificacao": "provisorio",
        "data_extracao": "2026-07-26",
    }


# ---------------------------------------------------------------------------
# Stub datetime — subclass of datetime.datetime, overrides now()
# ---------------------------------------------------------------------------

import datetime as _real_dt


class _StubDT(_real_dt.datetime):
    """Subclasse de datetime.datetime com now() controlável."""
    _fixed_now: _StubDT | None = None

    @classmethod
    def now(cls, tz=None):
        if cls._fixed_now is not None:
            if tz and cls._fixed_now.tzinfo is None:
                return cls._fixed_now.replace(tzinfo=tz)
            return cls._fixed_now
        return super().now(tz)

    @classmethod
    def freeze(cls, dt: datetime):
        cls._fixed_now = cls(
            dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second,
            dt.microsecond, tzinfo=dt.tzinfo,
        )

    @classmethod
    def unfreeze(cls):
        cls._fixed_now = None


# A Thursday (yesterday = Wednesday, not weekend)
_WEEKDAY_NOW = datetime(2026, 7, 23, 4, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class Harness:
    """Sets up all mocks for one test run. Call .run() to execute."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.store = _FakeStore()
        self.start_calls: list[dict] = []
        self.finish_calls: list[dict] = []
        self.run_return = "mock-run-id"
        self.mock_db = MagicMock()
        self.saved_ledger = None

        monkeypatch.setattr(od, "kg_store", self.store)

        mock_start = MagicMock(side_effect=self._capture_start)
        mock_finish = MagicMock(side_effect=self._capture_finish)
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        monkeypatch.setattr(od, "_get_db", lambda: self.mock_db)

        self._config_queries = ["query inovação"]
        self._structured_queries = [{"text": "query inovação", "family": "state_innovation_funding"}]
        monkeypatch.setattr(od.ws, "discovery_config", lambda: {
            "queries": list(self._config_queries),
            "max_results_per_query": 8,
            "max_candidates": 40,
            "max_dou_candidates": 80,
            "max_hub_children": 8,
            "reject_cache_ttl_days": 30,
        })
        monkeypatch.setattr(od.ws, "discovery_queries", lambda: list(self._structured_queries))

        self.search_hits: list[SearchHit] = []
        monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: list(self.search_hits))

        self.env: dict[str, str] = {}
        monkeypatch.setattr(od.os, "getenv", self._fake_getenv)

        self._dou_candidates: list[SearchHit] = []
        monkeypatch.setattr(dou_feeder, "dou_candidates", lambda day=None: list(self._dou_candidates))

        monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))

        self.triage_map: dict[str, dict | None] = {}

        def _fake_triage(hit, client, model):
            result = self.triage_map.get(hit.url)
            if result is None:
                return None
            return dict(result)
        monkeypatch.setattr(od, "_triage", _fake_triage)

        self.extract_map: dict[str, dict | None] = {}

        def _fake_extract(hit, page_text, agency, client, model):
            rec = self.extract_map.get(hit.url)
            if rec is None:
                return None
            return dict(rec)
        monkeypatch.setattr(od, "_extract", _fake_extract)

        monkeypatch.setattr(od, "_page_text", lambda hit: "texto da página")
        monkeypatch.setattr(od, "_expand_hub", lambda hit, known, max_c: [])

        monkeypatch.setattr(od, "_save_ledger",
                            lambda urls, rejected: setattr(self, "saved_ledger", (urls, rejected)))

        self.staged_records = None

        def _fake_stage(records):
            self.staged_records = list(records)
            return len(records)
        monkeypatch.setattr(od, "_stage_records", _fake_stage)

    def _fake_getenv(self, key, default=""):
        return self.env.get(key, default)

    def _capture_start(self, db, *, batch_id, source_key, mode):
        self.start_calls.append({"batch_id": batch_id, "source_key": source_key, "mode": mode})
        return self.run_return

    def _capture_finish(self, db, *, run_id, status, **kwargs):
        self.finish_calls.append({"run_id": run_id, "status": status, **kwargs})
        return True

    def enable_dou(self):
        """Enable DOU and set a weekday date so DOU is not skipped."""
        self.env["DISCOVERY_DOU_ENABLED"] = "1"
        _StubDT.freeze(_WEEKDAY_NOW)
        self.monkeypatch.setattr(od, "datetime", _StubDT)

    def run(self, write=True):
        return od.discover_opportunities(write=write)


# =============================================================================
# Tests
# =============================================================================

class TestOpenSearchChannel:
    """Open_search attribution with family."""

    def test_open_search_attribution(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        records = h.run()
        assert len(records) == 1
        r = records[0]
        assert r["discovery_channel"] == "open_search"
        assert r["query_family"] == "state_innovation_funding"
        assert r["origin_domain"] == "fap.sc.gov.br"

    def test_open_search_run_tracked(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        h.run()
        assert "open_search" in {s["source_key"] for s in h.start_calls}
        assert len(h.finish_calls) >= 1

    def test_multiple_queries_multiple_families(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h._config_queries = ["inovação estadual", "desafio corporativo"]
        h._structured_queries = [
            {"text": "inovação estadual", "family": "state_innovation_funding"},
            {"text": "desafio corporativo", "family": "corporate_open_innovation"},
        ]

        hits_by_q = {"inovação estadual": [_HIT_A], "desafio corporativo": [_HIT_B]}
        h.monkeypatch.setattr(od.websearch, "web_search",
                              lambda q, k=8: list(hits_by_q.get(q, [])))

        h.triage_map = {_HIT_A.url: _APPROVE, _HIT_B.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url),
                         _HIT_B.url: _make_record(_HIT_B.url)}
        records = h.run()
        families = {(r["url"], r["query_family"]) for r in records}
        assert ("https://fap.sc.gov.br/edital", "state_innovation_funding") in families
        assert ("https://empresa.com.br/desafio", "corporate_open_innovation") in families


class TestDouChannel:
    """DOU attribution without family."""

    def test_dou_attribution_no_family(self, monkeypatch):
        h = Harness(monkeypatch)
        h.enable_dou()
        h.search_hits = []
        h._dou_candidates = [_HIT_D]
        h.triage_map = {_HIT_D.url: _APPROVE}
        h.extract_map = {_HIT_D.url: _make_record(_HIT_D.url)}
        records = h.run()
        assert len(records) == 1
        r = records[0]
        assert r["discovery_channel"] == "dou"
        assert r["query_family"] is None
        assert r["origin_domain"] == "dou.gov.br"

    def test_dou_disabled_has_start(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.run()
        assert "dou" in {s["source_key"] for s in h.start_calls}


class TestHubExpansion:
    """Hub_expansion inherits family from parent."""

    def test_hub_child_inherits_family(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_HUB_CRAWL_ENABLED"] = "1"
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HUB_HIT]
        h.triage_map = {_HUB_HIT.url: _HUB_PARENT, _CHILD_HIT.url: _APPROVE}
        h.extract_map = {_CHILD_HIT.url: _make_record(_CHILD_HIT.url)}

        def _fake_expand(hit, known, max_c):
            return [_CHILD_HIT]
        h.monkeypatch.setattr(od, "_expand_hub", _fake_expand)

        records = h.run()
        assert len(records) == 1
        r = records[0]
        assert r["discovery_channel"] == "hub_expansion"
        assert r["query_family"] == "state_innovation_funding"
        assert r["origin_domain"] == "hub.com.br"

    def test_hub_disabled_no_expansion(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_HUB_CRAWL_ENABLED"] = "0"
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HUB_HIT]
        h.triage_map = {_HUB_HIT.url: _HUB_PARENT}

        def _fake_expand(hit, known, max_c):
            return [_CHILD_HIT]
        h.monkeypatch.setattr(od, "_expand_hub", _fake_expand)

        records = h.run()
        assert records == []


class TestOriginDomain:
    """origin_domain must be only hostname."""

    def test_domain_no_path(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        hit = SearchHit(title="Teste", url="https://exemplo.gov.br/edital",
                        snippet="teste", content="texto")
        h.search_hits = [hit]
        h.triage_map = {hit.url: _APPROVE}
        h.extract_map = {hit.url: _make_record(hit.url)}
        records = h.run()
        assert records[0]["origin_domain"] == "exemplo.gov.br"

    def test_domain_no_query(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        hit = SearchHit(title="Com Query", url="https://exemplo.gov.br/edital?p=1",
                        snippet="teste", content="texto")
        h.search_hits = [hit]
        h.triage_map = {hit.url: _APPROVE}
        h.extract_map = {hit.url: _make_record(hit.url)}
        records = h.run()
        assert records[0]["origin_domain"] == "exemplo.gov.br"
        assert "?" not in records[0]["origin_domain"]

    def test_domain_https_stripped(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        hit = SearchHit(title="Teste", url="https://sub.exemplo.gov.br/editais",
                        snippet="teste", content="texto")
        h.search_hits = [hit]
        h.triage_map = {hit.url: _APPROVE}
        h.extract_map = {hit.url: _make_record(hit.url)}
        records = h.run()
        assert records[0]["origin_domain"] == "sub.exemplo.gov.br"


class TestCrossChannelDedup:
    """First channel (DOU) wins on URL overlap."""

    def test_dou_wins_over_open_search(self, monkeypatch):
        h = Harness(monkeypatch)
        h.enable_dou()
        h._dou_candidates = [_HIT_D]
        h.search_hits = [_HIT_D]
        h.triage_map = {_HIT_D.url: _APPROVE}
        h.extract_map = {_HIT_D.url: _make_record(_HIT_D.url)}
        records = h.run()
        assert len(records) == 1
        assert records[0]["discovery_channel"] == "dou"

    def test_dedup_dou_first_then_open_search_skips(self, monkeypatch):
        """DOU sees HIT_A first, open_search tries same URL but skips."""
        h = Harness(monkeypatch)
        h.enable_dou()
        h._dou_candidates = [_HIT_A]
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        records = h.run()
        assert len(records) == 1
        assert records[0]["discovery_channel"] == "dou"


class TestNoCredentials:
    """Missing LLM → skipped for channels that had candidates."""

    def test_no_llm_returns_empty(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.monkeypatch.setattr(od, "_make_client", lambda role: (None, None))
        records = h.run()
        assert records == []

    def test_no_llm_skipped(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.monkeypatch.setattr(od, "_make_client", lambda role: (None, None))
        h.run()
        skipped = [f for f in h.finish_calls if f.get("status") == "skipped"]
        assert len(skipped) >= 1

    def test_no_llm_ledger_unchanged(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.monkeypatch.setattr(od, "_make_client", lambda role: (None, None))
        before = dict(h.store.ledger)
        h.run()
        assert h.store.ledger == before


class TestWeekendDou:
    """DOU on weekend → skipped."""

    def test_weekend_dou_does_not_process(self, monkeypatch):
        h = Harness(monkeypatch)
        # Use a Sunday so yesterday (Saturday) is weekend
        sunday = datetime(2026, 7, 26, 4, 0, 0, tzinfo=timezone.utc)
        _StubDT.freeze(sunday)
        monkeypatch.setattr(od, "datetime", _StubDT)

        h.env["DISCOVERY_DOU_ENABLED"] = "1"
        h._dou_candidates = [_HIT_D]
        h.run()
        # No dou records staged
        assert h.staged_records is None or len(h.staged_records) == 0


class TestEmptyResult:
    """No candidates → runs finish, empty return."""

    def test_empty_return(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = []
        records = h.run()
        assert records == []

    def test_finish_called(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = []
        h.run()
        assert len(h.finish_calls) >= 1

    def test_ledger_unchanged(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = []
        before = dict(h.store.ledger)
        h.run()
        assert h.store.ledger == before


class TestFailures:
    """Failures of query, triage, extraction → error_count but no crash."""

    def test_query_failure(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"

        def _raise(q, k=8):
            raise RuntimeError("API down")
        h.monkeypatch.setattr(od.websearch, "web_search", _raise)
        records = h.run()
        assert records == []

    def test_triage_failure(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: None}
        records = h.run()
        assert records == []

    def test_extraction_failure(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: None}
        records = h.run()
        assert records == []


class TestTelemetryUnavailable:
    """DB unavailable → runs not written, discovery still works."""

    def test_db_none_still_discovers(self, monkeypatch):
        h = Harness(monkeypatch)
        h.mock_db = None
        h.monkeypatch.setattr(od, "_get_db", lambda: None)
        h.run_return = None
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        records = h.run()
        assert len(records) == 1

    def test_db_none_no_source_runs(self, monkeypatch):
        h = Harness(monkeypatch)
        h.mock_db = None
        h.monkeypatch.setattr(od, "_get_db", lambda: None)
        h.run_return = None
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        h.run()
        assert len(h.start_calls) == 3
        assert len(h.finish_calls) == 0


class TestPublicReturn:
    """Return type, staging, and ledger preserved."""

    def test_returns_list_of_dicts(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        records = h.run()
        assert isinstance(records, list)
        assert all(isinstance(r, dict) for r in records)

    def test_ledger_saved(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        h.run()
        assert h.saved_ledger is not None
        urls, _ = h.saved_ledger
        tracked = od._norm_url(_HIT_A.url)
        assert any(tracked in u for u in urls)

    def test_stage_called_with_attribution(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        h.run()
        assert h.staged_records is not None
        assert len(h.staged_records) == 1
        r = h.staged_records[0]
        assert "discovery_run_id" in r
        assert "discovery_channel" in r
        assert "query_family" in r
        assert "origin_domain" in r

    def test_dry_run_no_staging(self, monkeypatch):
        h = Harness(monkeypatch)
        h.env["DISCOVERY_DOU_ENABLED"] = "0"
        h.search_hits = [_HIT_A]
        h.triage_map = {_HIT_A.url: _APPROVE}
        h.extract_map = {_HIT_A.url: _make_record(_HIT_A.url)}
        records = h.run(write=False)
        assert h.staged_records is None
        assert len(records) == 1
