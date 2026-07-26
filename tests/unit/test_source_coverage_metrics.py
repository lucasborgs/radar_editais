"""Testes do read model de funil editorial, lacunas e domínios emergentes (RT03-T05).

Cobre:
  - denominador presente e ausente (yield_rate)
  - canal/família e bucket não atribuído
  - todos os estados de saúde e sua precedência
  - zero ambíguo
  - stale após duas janelas
  - flag desligada
  - domínio com 0, 1 e 2 aprovações
  - limite temporal de 90 dias
  - rejeitados e pendentes não viram candidatos
  - ausência total de side effects
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from radar.core.services.source_coverage_metrics import (
    SourceCoverageReport,
    compute_channel_editorial_funnels,
    compute_channel_run_metrics,
    compute_emerging_domains,
    compute_family_editorial_funnels,
    compute_source_coverage,
    derive_channel_healths,
    detect_gaps,
)

pytestmark = pytest.mark.unit


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _run(
    source_key: str,
    status: str,
    started_at: str,
    completed_at: str | None = None,
    records_observed: int | None = None,
    records_emitted: int | None = None,
    records_staged: int | None = None,
) -> dict:
    return {
        "source_key": source_key,
        "status": status,
        "started_at": _dt(started_at),
        "completed_at": _dt(completed_at) if completed_at else None,
        "records_observed": records_observed,
        "records_emitted": records_emitted,
        "records_staged": records_staged,
        "error_count": 0,
        "reason_code": None,
        "metrics": {},
    }


def _disc(
    status: str = "pending",
    discovery_channel: str | None = "open_search",
    query_family: str | None = None,
    origin_domain: str | None = None,
    created_at: str | None = None,
    reviewed_at: str | None = None,
) -> dict:
    return {
        "status": status,
        "discovery_channel": discovery_channel,
        "query_family": query_family,
        "origin_domain": origin_domain,
        "created_at": _dt(created_at) if created_at else None,
        "reviewed_at": _dt(reviewed_at) if reviewed_at else None,
    }


_CHANNELS = [
    {"source_key": "finep", "mode": "dedicated", "expected_interval_hours": 24,
     "enabled_by_default": True, "display_name": "FINEP", "scope_note": "x"},
    {"source_key": "dou", "mode": "official_feed", "expected_interval_hours": 24,
     "enabled_by_default": False, "flag_name": "DISCOVERY_DOU_ENABLED",
     "display_name": "DOU", "scope_note": "x"},
    {"source_key": "open_search", "mode": "open_search", "expected_interval_hours": 24,
     "enabled_by_default": True, "display_name": "Busca aberta", "scope_note": "x"},
    {"source_key": "hub_expansion", "mode": "hub", "expected_interval_hours": 24,
     "enabled_by_default": False, "flag_name": "DISCOVERY_HUB_CRAWL_ENABLED",
     "display_name": "Hubs", "scope_note": "x"},
]

_FAMILY_KEYS = ["state_innovation_funding", "corporate_open_innovation"]

_REF = _dt("2026-07-26T12:00:00+00:00")


# ══════════════════════════════════════════════════════════════════════════
# 1. Run metrics — denominador
# ══════════════════════════════════════════════════════════════════════════


class TestRunMetricsDenominator:

    REF = _dt("2026-07-26T12:00:00+00:00")

    def test_denominator_present(self):
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=20, records_emitted=10, records_staged=5),
        ]
        m = compute_channel_run_metrics(runs)
        assert m.last_attempt == _dt("2026-07-26T10:00:00+00:00")
        assert m.last_success == _dt("2026-07-26T10:30:00+00:00")
        assert m.total_records_observed == 20
        assert m.total_records_emitted == 10
        assert m.total_records_staged == 5
        assert m.yield_rate == 0.5

    def test_denominator_absent_emitted_none(self):
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=20, records_emitted=None, records_staged=5),
        ]
        m = compute_channel_run_metrics(runs)
        assert m.total_records_emitted is None
        assert m.yield_rate is None

    def test_denominator_absent_emitted_zero(self):
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=0, records_emitted=0, records_staged=0),
        ]
        m = compute_channel_run_metrics(runs)
        assert m.total_records_emitted == 0
        assert m.yield_rate is None

    def test_empty_runs_returns_nulls(self):
        m = compute_channel_run_metrics([])
        assert m.last_attempt is None
        assert m.last_success is None
        assert m.total_records_observed is None
        assert m.total_records_emitted is None
        assert m.total_records_staged is None
        assert m.yield_rate is None

    def test_aggregates_multiple_runs(self):
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=20, records_emitted=10, records_staged=5),
            _run("finep", "succeeded", "2026-07-25T10:00:00+00:00",
                 completed_at="2026-07-25T10:30:00+00:00",
                 records_observed=10, records_emitted=8, records_staged=3),
        ]
        m = compute_channel_run_metrics(runs)
        assert m.total_records_observed == 30
        assert m.total_records_emitted == 18
        assert m.total_records_staged == 8
        assert m.yield_rate == pytest.approx(8 / 18)

    def test_last_attempt_and_success_from_latest(self):
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00"),
            _run("finep", "failed", "2026-07-25T10:00:00+00:00",
                 completed_at="2026-07-25T10:30:00+00:00"),
            _run("finep", "succeeded", "2026-07-24T10:00:00+00:00",
                 completed_at="2026-07-24T10:30:00+00:00"),
        ]
        m = compute_channel_run_metrics(runs)
        assert m.last_attempt == _dt("2026-07-26T10:00:00+00:00")
        assert m.last_success == _dt("2026-07-26T10:30:00+00:00")

    def test_partial_never_becomes_last_success(self):
        runs = [
            _run("finep", "partial", "2026-07-26T11:00:00+00:00",
                 completed_at="2026-07-26T11:30:00+00:00", records_observed=5),
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00", records_observed=5),
        ]
        assert compute_channel_run_metrics(runs).last_success == _dt(
            "2026-07-26T10:30:00+00:00"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. Editorial funnel — canal e não atribuído
# ══════════════════════════════════════════════════════════════════════════


class TestEditorialFunnel:

    REF = _dt("2026-07-26T12:00:00+00:00")

    def test_by_channel(self):
        discovered = [
            _disc(status="promoted", discovery_channel="open_search",
                  created_at="2026-07-25T10:00:00+00:00",
                  reviewed_at="2026-07-25T12:00:00+00:00"),
            _disc(status="rejected", discovery_channel="open_search",
                  created_at="2026-07-24T10:00:00+00:00",
                  reviewed_at="2026-07-24T12:00:00+00:00"),
            _disc(status="pending", discovery_channel="open_search"),
            _disc(status="promoted", discovery_channel="dou"),
        ]
        result = compute_channel_editorial_funnels(
            discovered, ["open_search", "dou", "finep"]
        )
        os_funnel = result["open_search"]
        assert os_funnel.approved == 1
        assert os_funnel.rejected == 1
        assert os_funnel.pending == 1
        assert os_funnel.approval_rate == 0.5
        assert os_funnel.avg_review_hours == 2.0

        dou_funnel = result["dou"]
        assert dou_funnel.approved == 1
        assert dou_funnel.rejected == 0
        assert dou_funnel.pending == 0
        assert dou_funnel.approval_rate == 1.0

        finep_funnel = result["finep"]
        assert finep_funnel.approved == 0
        assert finep_funnel.rejected == 0
        assert finep_funnel.pending == 0
        assert finep_funnel.approval_rate is None

    def test_unassigned_bucket(self):
        """Linhas legadas (discovery_channel IS NULL) aparecem como não atribuídas."""
        discovered = [
            _disc(status="promoted", discovery_channel=None),
            _disc(status="rejected", discovery_channel=None),
            _disc(status="pending", discovery_channel=None),
            _disc(status="promoted", discovery_channel="open_search"),
        ]
        result = compute_channel_editorial_funnels(
            discovered, ["open_search"]
        )
        unassigned = result["__unassigned__"]
        assert unassigned.approved == 1
        assert unassigned.rejected == 1
        assert unassigned.pending == 1

        os_funnel = result["open_search"]
        assert os_funnel.approved == 1

    def test_no_denominator_returns_none_rate(self):
        discovered = [
            _disc(status="pending", discovery_channel="open_search"),
        ]
        result = compute_channel_editorial_funnels(
            discovered, ["open_search"]
        )
        assert result["open_search"].approval_rate is None


class TestFamilyFunnel:

    REF = _dt("2026-07-26T12:00:00+00:00")

    def test_by_family(self):
        discovered = [
            _disc(status="promoted", query_family="state_innovation_funding",
                  created_at="2026-07-25T10:00:00+00:00",
                  reviewed_at="2026-07-25T12:00:00+00:00"),
            _disc(status="rejected", query_family="state_innovation_funding"),
            _disc(status="promoted", query_family="corporate_open_innovation"),
            _disc(status="pending", query_family=None),
        ]
        result = compute_family_editorial_funnels(
            discovered,
            ["state_innovation_funding", "corporate_open_innovation"],
        )
        sif = result["state_innovation_funding"]
        assert sif.approved == 1
        assert sif.rejected == 1
        assert sif.approval_rate == 0.5
        assert sif.avg_review_hours == 2.0

        coi = result["corporate_open_innovation"]
        assert coi.approved == 1
        assert coi.rejected == 0
        assert coi.approval_rate == 1.0

    def test_unassigned_bucket_includes_legacy_rows(self):
        discovered = [
            _disc(status="promoted", query_family=None,
                  created_at="2026-07-25T10:00:00+00:00",
                  reviewed_at="2026-07-25T12:00:00+00:00"),
            _disc(status="rejected", query_family=None),
            _disc(status="pending", query_family=None),
        ]
        result = compute_family_editorial_funnels(discovered, _FAMILY_KEYS)

        unassigned = result["__unassigned__"]
        assert (unassigned.approved, unassigned.rejected, unassigned.pending) == (1, 1, 1)
        assert unassigned.approval_rate == 0.5
        assert unassigned.avg_review_hours == 2.0


# ══════════════════════════════════════════════════════════════════════════
# 3. Health — precedence
# ══════════════════════════════════════════════════════════════════════════


class TestHealthPrecedence:

    REF = _dt("2026-07-26T12:00:00+00:00")

    def test_disabled(self):
        healths = derive_channel_healths(
            [_CHANNELS[1]],  # dou (flag gated, default off)
            {},
            self.REF,
            env={"DISCOVERY_DOU_ENABLED": "0"},
        )
        assert healths["dou"] == "disabled"

    def test_disabled_overrides_failing(self):
        """disabled tem precedência sobre failing mesmo com run failed."""
        healths = derive_channel_healths(
            [_CHANNELS[1]],  # dou
            {"dou": [_run("dou", "failed", "2026-07-26T10:00:00+00:00")]},
            self.REF,
            env={"DISCOVERY_DOU_ENABLED": "0"},
        )
        assert healths["dou"] == "disabled"

    def test_failing(self):
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "failed", "2026-07-26T10:00:00+00:00")]},
            self.REF,
        )
        assert healths["finep"] == "failing"

    def test_degraded(self):
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "partial", "2026-07-26T10:00:00+00:00",
                           completed_at="2026-07-26T10:30:00+00:00",
                           records_observed=5)]},
            self.REF,
        )
        assert healths["finep"] == "degraded"

    def test_stale_two_windows(self):
        """Último sucesso > 2*interval_horas atrás → stale."""
        three_days_ago = self.REF - timedelta(hours=72)
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded",
                           (three_days_ago - timedelta(hours=1)).isoformat(),
                           completed_at=three_days_ago.isoformat(),
                           records_observed=5)]},
            self.REF,
        )
        assert healths["finep"] == "stale"

    def test_not_stale_within_two_windows(self):
        """Sucesso dentro de 2*interval (mas fora de 1*interval) → unknown, não stale."""
        thirty_hours_ago = self.REF - timedelta(hours=30)
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded",
                           (thirty_hours_ago - timedelta(hours=1)).isoformat(),
                           completed_at=thirty_hours_ago.isoformat(),
                           records_observed=5)]},
            self.REF,
        )
        assert healths["finep"] == "unknown"

    def test_healthy(self):
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded",
                           "2026-07-26T10:00:00+00:00",
                           completed_at="2026-07-26T10:30:00+00:00",
                           records_observed=5)]},
            self.REF,
        )
        assert healths["finep"] == "healthy"

    def test_healthy_uses_completed_at_not_started_at(self):
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded", "2026-07-24T10:00:00+00:00",
                             completed_at="2026-07-26T10:30:00+00:00",
                             records_observed=5)]},
            self.REF,
        )
        assert healths["finep"] == "healthy"

    def test_partial_is_not_a_healthy_history_entry(self):
        old_success = self.REF - timedelta(hours=72)
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [
                _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                     completed_at="2026-07-26T10:30:00+00:00", records_observed=0),
                _run("finep", "partial", "2026-07-25T10:00:00+00:00",
                     completed_at="2026-07-25T10:30:00+00:00", records_observed=5),
                _run("finep", "succeeded", (old_success - timedelta(hours=1)).isoformat(),
                     completed_at=old_success.isoformat(), records_observed=5),
            ]},
            self.REF,
        )
        assert healths["finep"] == "stale"

    def test_unknown_no_runs(self):
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {},
            self.REF,
        )
        assert healths["finep"] == "unknown"

    def test_ambiguous_zero(self):
        """Succeeded mas records_observed=0 → unknown."""
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded",
                           "2026-07-26T10:00:00+00:00",
                           completed_at="2026-07-26T10:30:00+00:00",
                           records_observed=0)]},
            self.REF,
        )
        assert healths["finep"] == "unknown"

    def test_ambiguous_zero_staged_with_observed(self):
        """records_observed>0 é suficiente para resultado observável."""
        healths = derive_channel_healths(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded",
                           "2026-07-26T10:00:00+00:00",
                           completed_at="2026-07-26T10:30:00+00:00",
                           records_observed=3, records_staged=0)]},
            self.REF,
        )
        assert healths["finep"] == "healthy"

    def test_disabled_via_flag_env_off(self):
        """hub_expansion com flag desligada → disabled."""
        healths = derive_channel_healths(
            [_CHANNELS[3]],
            {},
            self.REF,
            env={"DISCOVERY_HUB_CRAWL_ENABLED": "0"},
        )
        assert healths["hub_expansion"] == "disabled"

    def test_enabled_via_flag_env_on(self):
        """hub_expansion com flag ligada → unknown (sem runs)."""
        healths = derive_channel_healths(
            [_CHANNELS[3]],
            {},
            self.REF,
            env={"DISCOVERY_HUB_CRAWL_ENABLED": "1"},
        )
        assert healths["hub_expansion"] == "unknown"

    def test_full_precedence_chain(self):
        """Testa todos os estados em sequência."""
        for ch_cfg, runs, env, expected in [
            (_CHANNELS[1], {}, {"DISCOVERY_DOU_ENABLED": "0"}, "disabled"),
            (_CHANNELS[0], [_run("finep", "failed",
                                 "2026-07-26T10:00:00+00:00")], {}, "failing"),
            (_CHANNELS[0], [_run("finep", "partial",
                                 "2026-07-26T10:00:00+00:00",
                                 completed_at="2026-07-26T10:30:00+00:00",
                                 records_observed=3)], {}, "degraded"),
            (_CHANNELS[0], [], {}, "unknown"),
        ]:
            healths = derive_channel_healths(
                [ch_cfg],
                {ch_cfg["source_key"]: runs},
                self.REF,
                env=env or None,
            )
            sk = ch_cfg["source_key"]
            assert healths[sk] == expected, f"expected {expected} for {sk}"


# ══════════════════════════════════════════════════════════════════════════
# 4. Gaps
# ══════════════════════════════════════════════════════════════════════════


class TestGaps:

    REF = _dt("2026-07-26T12:00:00+00:00")

    def test_enabled_no_run(self):
        """Canal habilitado sem run → gap."""
        gaps = detect_gaps(
            [_CHANNELS[0]], {}, [], _FAMILY_KEYS, self.REF,
        )
        signals = [(g.source_key, g.signal) for g in gaps]
        assert ("finep", "enabled_no_run") in signals

    def test_disabled_no_run_no_gap(self):
        """Canal desabilitado sem run → sem gap enabled_no_run."""
        gaps = detect_gaps(
            [_CHANNELS[1]], {}, [], _FAMILY_KEYS, self.REF,
            env={"DISCOVERY_DOU_ENABLED": "0"},
        )
        signals = [g.signal for g in gaps]
        assert "enabled_no_run" not in signals

    def test_ambiguous_run(self):
        gaps = detect_gaps(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded",
                           "2026-07-26T10:00:00+00:00",
                           completed_at="2026-07-26T10:30:00+00:00",
                           records_observed=0, records_staged=0)]},
            [], _FAMILY_KEYS, self.REF,
        )
        signals = [(g.source_key, g.signal) for g in gaps]
        assert ("finep", "ambiguous_run") in signals

    def test_delayed(self):
        """Última run concluída > expected_interval_hours atrás."""
        thirty_hours_ago = self.REF - timedelta(hours=30)
        gaps = detect_gaps(
            [_CHANNELS[0]],
            {"finep": [_run("finep", "succeeded",
                           (thirty_hours_ago - timedelta(hours=1)).isoformat(),
                           completed_at=thirty_hours_ago.isoformat(),
                           records_observed=5)]},
            [], _FAMILY_KEYS, self.REF,
        )
        signals = [(g.source_key, g.signal) for g in gaps]
        assert ("finep", "delayed") in signals

    def test_family_no_denominator(self):
        gaps = detect_gaps(
            [_CHANNELS[2]],
            {"open_search": [_run("open_search", "succeeded",
                                  "2026-07-26T10:00:00+00:00",
                                  records_observed=5)]},
            [_disc(status="pending", discovery_channel="open_search",
                   query_family="state_innovation_funding")],
            _FAMILY_KEYS, self.REF,
        )
        signals = [g.signal for g in gaps]
        assert "family_no_denominator" in signals

    def test_pending_queue(self):
        gaps = detect_gaps(
            [_CHANNELS[2]],
            {"open_search": [_run("open_search", "succeeded",
                                  "2026-07-26T10:00:00+00:00",
                                  records_observed=5)]},
            [_disc(status="pending", discovery_channel="open_search")],
            _FAMILY_KEYS, self.REF,
        )
        signals = [g.signal for g in gaps]
        assert "pending_queue" in signals

    def test_no_gaps_healthy_channel(self):
        """Canal saudável sem lacunas; outras famílias sem denominador geram gap."""
        gaps = detect_gaps(
            [_CHANNELS[2]],
            {"open_search": [_run("open_search", "succeeded",
                                  "2026-07-26T10:00:00+00:00",
                                  completed_at="2026-07-26T10:30:00+00:00",
                                  records_observed=5, records_emitted=3,
                                  records_staged=2)]},
            [_disc(status="promoted", discovery_channel="open_search",
                   query_family="state_innovation_funding")],
            _FAMILY_KEYS, self.REF,
        )
        channel_gaps = [g for g in gaps if g.source_key == "open_search"]
        family_gaps = [g for g in gaps if g.signal == "family_no_denominator"]
        assert len(channel_gaps) == 0
        assert len(family_gaps) == 1
        assert family_gaps[0].source_key == "corporate_open_innovation"


# ══════════════════════════════════════════════════════════════════════════
# 5. Emerging domains
# ══════════════════════════════════════════════════════════════════════════


class TestEmergingDomains:

    REF = _dt("2026-07-26T12:00:00+00:00")

    def test_domain_with_two_approvals_is_candidate(self):
        discovered = [
            _disc(status="promoted", origin_domain="exemplo.gov.br",
                  reviewed_at="2026-07-20T10:00:00+00:00"),
            _disc(status="promoted", origin_domain="exemplo.gov.br",
                  reviewed_at="2026-07-21T10:00:00+00:00"),
        ]
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 1
        d = domains[0]
        assert d.domain == "exemplo.gov.br"
        assert d.approval_count == 2
        assert d.candidate_for_dedicated_monitoring is True

    def test_domain_with_one_approval_not_candidate(self):
        discovered = [
            _disc(status="promoted", origin_domain="unico.gov.br",
                  reviewed_at="2026-07-20T10:00:00+00:00"),
        ]
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 1
        assert domains[0].candidate_for_dedicated_monitoring is False

    def test_domain_with_zero_approvals_empty(self):
        discovered: list = []
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 0

    def test_rejected_not_counted(self):
        discovered = [
            _disc(status="rejected", origin_domain="rejeitado.gov.br",
                  reviewed_at="2026-07-20T10:00:00+00:00"),
            _disc(status="rejected", origin_domain="rejeitado.gov.br",
                  reviewed_at="2026-07-21T10:00:00+00:00"),
        ]
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 0

    def test_pending_not_counted(self):
        discovered = [
            _disc(status="pending", origin_domain="pendente.gov.br"),
        ]
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 0

    def test_outside_90_days_window(self):
        old_date = (self.REF - timedelta(days=91)).isoformat()
        discovered = [
            _disc(status="promoted", origin_domain="velho.gov.br",
                  reviewed_at=old_date),
            _disc(status="promoted", origin_domain="velho.gov.br",
                  reviewed_at=old_date),
        ]
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 0

    def test_mixed_domains(self):
        discovered = [
            _disc(status="promoted", origin_domain="candidato.gov.br",
                  reviewed_at="2026-07-20T10:00:00+00:00"),
            _disc(status="promoted", origin_domain="candidato.gov.br",
                  reviewed_at="2026-07-21T10:00:00+00:00"),
            _disc(status="promoted", origin_domain="unico.gov.br",
                  reviewed_at="2026-07-22T10:00:00+00:00"),
            _disc(status="promoted", origin_domain="candidato.gov.br",
                  reviewed_at="2026-07-23T10:00:00+00:00"),
        ]
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 2
        by_domain = {d.domain: d for d in domains}
        assert by_domain["candidato.gov.br"].candidate_for_dedicated_monitoring is True
        assert by_domain["candidato.gov.br"].approval_count == 3
        assert by_domain["unico.gov.br"].candidate_for_dedicated_monitoring is False
        assert by_domain["unico.gov.br"].approval_count == 1

    def test_boundary_90_days_exactly(self):
        exactly_90_ago = self.REF - timedelta(days=90)
        discovered = [
            _disc(status="promoted", origin_domain="limite.gov.br",
                  reviewed_at=exactly_90_ago.isoformat()),
            _disc(status="promoted", origin_domain="limite.gov.br",
                  reviewed_at=exactly_90_ago.isoformat()),
        ]
        domains = compute_emerging_domains(discovered, self.REF)
        assert len(domains) == 1
        assert domains[0].candidate_for_dedicated_monitoring is True

    def test_domain_normalization_and_invalid_values(self):
        discovered = [
            _disc(status="promoted", origin_domain="EXEMPLO.GOV.BR.",
                  reviewed_at="2026-07-20T10:00:00+00:00"),
            _disc(status="promoted", origin_domain="exemplo.gov.br",
                  reviewed_at="2026-07-21T10:00:00+00:00"),
            *[
                _disc(status="promoted", origin_domain=value,
                      reviewed_at="2026-07-21T10:00:00+00:00")
                for value in [
                    "https://url.gov.br", "path.gov.br/edital",
                    "user@host.gov.br", "host.gov.br:443",
                ]
            ],
        ]
        domains = compute_emerging_domains(discovered, self.REF)

        assert [(domain.domain, domain.approval_count) for domain in domains] == [
            ("exemplo.gov.br", 2)
        ]

    def test_future_approval_is_not_counted(self):
        discovered = [
            _disc(status="promoted", origin_domain="futuro.gov.br",
                  reviewed_at="2026-07-27T10:00:00+00:00"),
        ]
        assert compute_emerging_domains(discovered, self.REF) == []

    def test_no_side_effects(self):
        """Testa que a função não modifica os dados de entrada."""
        discovered = [
            _disc(status="promoted", origin_domain="exemplo.gov.br",
                  reviewed_at="2026-07-20T10:00:00+00:00"),
        ]
        original_len = len(discovered)
        _ = compute_emerging_domains(discovered, self.REF)
        assert len(discovered) == original_len
        assert discovered[0]["status"] == "promoted"


# ══════════════════════════════════════════════════════════════════════════
# 6. Integration — compute_source_coverage
# ══════════════════════════════════════════════════════════════════════════


class TestSourceCoverageReport:

    REF = _dt("2026-07-26T12:00:00+00:00")

    def test_full_report_no_side_effects(self):
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=10, records_emitted=8, records_staged=3),
            _run("open_search", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=20, records_emitted=15, records_staged=7),
        ]
        discovered = [
            _disc(status="promoted", discovery_channel="open_search",
                  query_family="state_innovation_funding",
                  origin_domain="op.gov.br",
                  created_at="2026-07-25T10:00:00+00:00",
                  reviewed_at="2026-07-25T12:00:00+00:00"),
            _disc(status="pending", discovery_channel="open_search",
                  query_family="state_innovation_funding"),
            _disc(status="promoted", discovery_channel="open_search",
                  query_family="corporate_open_innovation",
                  origin_domain="empresa.com.br",
                  created_at="2026-07-24T10:00:00+00:00",
                  reviewed_at="2026-07-24T14:00:00+00:00"),
        ]
        report = compute_source_coverage(
            runs, discovered, _CHANNELS, _FAMILY_KEYS, self.REF,
        )

        assert isinstance(report, SourceCoverageReport)
        assert report.reference_date == self.REF

        # 1. Run metrics
        assert "finep" in report.channel_runs
        assert "open_search" in report.channel_runs
        assert report.channel_runs["finep"].yield_rate == 3 / 8

        # 2. Funnel
        assert "open_search" in report.channel_funnel
        assert report.channel_funnel["open_search"].approved == 2
        assert report.channel_funnel["open_search"].pending == 1

        assert "state_innovation_funding" in report.family_funnel
        assert report.family_funnel["state_innovation_funding"].approved == 1

        # 3. Health
        assert report.channel_health["finep"] == "healthy"
        assert report.channel_health["dou"] == "disabled"
        assert report.channel_health["open_search"] == "healthy"

        # 4. Gaps
        gap_signals = [(g.source_key, g.signal) for g in report.gaps]
        assert ("hub_expansion", "enabled_no_run") not in gap_signals
        assert ("finep", "enabled_no_run") not in gap_signals

        # 5. Emerging domains
        assert len(report.emerging_domains) >= 2
        ed_by_domain = {d.domain: d for d in report.emerging_domains}
        assert "op.gov.br" in ed_by_domain
        assert "empresa.com.br" in ed_by_domain
        assert ed_by_domain["op.gov.br"].candidate_for_dedicated_monitoring is False
        assert ed_by_domain["empresa.com.br"].candidate_for_dedicated_monitoring is False

    def test_empty_report(self):
        report = compute_source_coverage(
            [], [], _CHANNELS, _FAMILY_KEYS, self.REF,
        )
        assert isinstance(report, SourceCoverageReport)
        for sk in ["finep", "dou", "open_search", "hub_expansion"]:
            assert sk in report.channel_runs
            assert sk in report.channel_funnel
            assert sk in report.channel_health
        assert len(report.emerging_domains) == 0

    def test_mixed_naive_and_utc_timestamps_are_normalized(self):
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00",
                 completed_at="2026-07-26T10:30:00", records_observed=5),
            _run("finep", "failed", "2026-07-25T10:00:00+00:00"),
        ]
        report = compute_source_coverage(runs, [], [_CHANNELS[0]], [], self.REF)

        assert report.channel_runs["finep"].last_attempt == _dt(
            "2026-07-26T10:00:00+00:00"
        )
        assert report.channel_health["finep"] == "healthy"

    def test_input_runs_unchanged(self):
        """Garante side-effect free: inputs não são modificados."""
        runs = [
            _run("finep", "succeeded", "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00", records_observed=5),
        ]
        discovered = [
            _disc(status="promoted", discovery_channel="open_search",
                  origin_domain="op.gov.br",
                  reviewed_at="2026-07-25T10:00:00+00:00"),
        ]
        runs_copy = [dict(r) for r in runs]
        disc_copy = [dict(d) for d in discovered]

        _ = compute_source_coverage(
            runs, discovered, _CHANNELS, _FAMILY_KEYS, self.REF,
        )
        assert runs == runs_copy
        assert discovered == disc_copy
