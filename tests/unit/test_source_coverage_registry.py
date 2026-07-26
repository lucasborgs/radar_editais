"""Testes do registry de canais e famílias de busca (RT03-T01).

Valida:
  - _coverage.md carrega 7 canais com source_key/mode corretos;
  - _discovery.md carrega 4 famílias de busca;
  - invariantes: lowercase, unicidade, modos canônicos, fluxos sem flag;
  - fixtures inválidas acionam ValueError;
  - compatibilidade: discovery_config() ainda devolve queries planas;
  - código não mantém lista normativa paralela.
"""
from __future__ import annotations

import pytest

from radar.core.kg.schema import (
    clear_cache,
    coverage_channel,
    coverage_channels,
    discovery_config,
    query_families,
)

pytestmark = pytest.mark.unit

# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clear_caches():
    """Isolamento entre testes: limpa caches do schema loader."""
    clear_cache()
    yield


# ══════════════════════════════════════════════════════════════════════════
# Channels — contrato RT03-T01
# ══════════════════════════════════════════════════════════════════════════


class TestChannelsContract:
    """Validates the 7-channel contract from _coverage.md."""

    EXPECTED_KEYS = {
        "finep", "fapesp", "fapesc",
        "web_curated", "open_search", "dou", "hub_expansion",
    }
    EXPECTED_MODES: dict[str, str] = {
        "finep": "dedicated",
        "fapesp": "dedicated",
        "fapesc": "dedicated",
        "web_curated": "curated_web",
        "open_search": "open_search",
        "dou": "official_feed",
        "hub_expansion": "hub",
    }

    def test_exactly_seven_channels(self):
        channels = coverage_channels()
        assert len(channels) == 7, f"expected 7 channels, got {len(channels)}"

    def test_all_expected_keys_present(self):
        keys = {ch["source_key"] for ch in coverage_channels()}
        assert keys == self.EXPECTED_KEYS, f"missing: {self.EXPECTED_KEYS - keys}"

    def test_all_modes_correct(self):
        for ch in coverage_channels():
            key = ch["source_key"]
            expected = self.EXPECTED_MODES[key]
            assert ch["mode"] == expected, f"{key}: expected mode {expected}, got {ch['mode']}"

    def test_every_channel_has_display_name(self):
        for ch in coverage_channels():
            assert ch.get("display_name"), f"{ch['source_key']} missing display_name"

    def test_every_channel_has_scope_note(self):
        for ch in coverage_channels():
            assert ch.get("scope_note"), f"{ch['source_key']} missing scope_note"

    def test_every_channel_has_expected_interval(self):
        for ch in coverage_channels():
            assert "expected_interval_hours" in ch, f"{ch['source_key']} missing expected_interval_hours"

    def test_every_channel_has_enabled_by_default(self):
        for ch in coverage_channels():
            assert "enabled_by_default" in ch, f"{ch['source_key']} missing enabled_by_default"

    def test_channel_allows_flag_name_only_for_gated(self):
        """Canais com flag registram o nome, não o valor."""
        gated = {"dou": "DISCOVERY_DOU_ENABLED", "hub_expansion": "DISCOVERY_HUB_CRAWL_ENABLED"}
        for ch in coverage_channels():
            key = ch["source_key"]
            if key in gated:
                assert ch.get("flag_name") == gated[key], f"{key} expected flag_name={gated[key]}"
            else:
                assert "flag_name" not in ch, f"{key} should not have flag_name"

    def test_source_key_lowercase(self):
        for ch in coverage_channels():
            key = ch["source_key"]
            assert key == key.lower(), f"source_key {key!r} is not lowercase"

    def test_open_search_is_logical_not_tavily(self):
        """open_search não nomeia Tavily como canal normativo."""
        ch = coverage_channel("open_search")
        assert ch is not None
        assert ch["mode"] == "open_search"
        assert "tavily" not in ch.get("scope_note", "").lower()

    def test_no_upstream_search_provider_in_contract(self):
        """Nenhum canal referencia provider de busca upstream."""
        for ch in coverage_channels():
            note = ch.get("scope_note", "")
            assert "tavily" not in note.lower(), f"{ch['source_key']} mentions tavily in scope_note"

    def test_lookup_by_key(self):
        ch = coverage_channel("finep")
        assert ch is not None
        assert ch["source_key"] == "finep"
        assert ch["mode"] == "dedicated"

    def test_lookup_missing_returns_none(self):
        assert coverage_channel("nonexistent") is None


# ══════════════════════════════════════════════════════════════════════════
# Channels — validation / edge cases
# ══════════════════════════════════════════════════════════════════════════


class TestChannelsValidation:

    def test_duplicate_source_key_raises(self, monkeypatch):
        """Duplicata dentro do YAML é erro de validação."""
        bad = {
            "channels": [
                {"source_key": "finep", "mode": "dedicated"},
                {"source_key": "finep", "mode": "dedicated"},
            ]
        }
        monkeypatch.setattr("radar.core.kg.schema.coverage_config", lambda: bad)
        with pytest.raises(ValueError, match="duplicate source_key"):
            coverage_channels()

    def test_invalid_mode_raises(self, monkeypatch):
        bad = {
            "channels": [
                {"source_key": "bad_mode", "mode": "invalid_mode"},
            ]
        }
        monkeypatch.setattr("radar.core.kg.schema.coverage_config", lambda: bad)
        with pytest.raises(ValueError, match="invalid mode"):
            coverage_channels()

    def test_uppercase_source_key_raises(self, monkeypatch):
        bad = {
            "channels": [
                {"source_key": "Finep", "mode": "dedicated"},
            ]
        }
        monkeypatch.setattr("radar.core.kg.schema.coverage_config", lambda: bad)
        with pytest.raises(ValueError, match="source_key must be lowercase"):
            coverage_channels()


# ══════════════════════════════════════════════════════════════════════════
# Query families — contrato RT03-T01
# ══════════════════════════════════════════════════════════════════════════


class TestQueryFamiliesContract:

    EXPECTED_FAMILIES = {
        "state_innovation_funding",
        "corporate_open_innovation",
        "startup_acceleration",
        "international_brazil_access",
    }

    def test_exactly_four_families(self):
        families = query_families()
        assert len(families) == 4, f"expected 4 families, got {len(families)}"

    def test_all_expected_families_present(self):
        keys = {fam["key"] for fam in query_families()}
        assert keys == self.EXPECTED_FAMILIES, f"missing: {self.EXPECTED_FAMILIES - keys}"

    def test_every_family_has_description(self):
        for fam in query_families():
            assert fam.get("description"), f"{fam['key']} missing description"

    def test_family_keys_lowercase(self):
        for fam in query_families():
            key = fam["key"]
            assert key == key.lower(), f"family key {key!r} is not lowercase"


# ══════════════════════════════════════════════════════════════════════════
# Query families — validation / edge cases
# ══════════════════════════════════════════════════════════════════════════


class TestQueryFamiliesValidation:

    def test_duplicate_family_key_raises(self, monkeypatch):
        bad = {
            "query_families": [
                {"key": "state_innovation_funding", "description": "dup"},
                {"key": "state_innovation_funding", "description": "dup2"},
            ]
        }
        monkeypatch.setattr("radar.core.kg.schema.discovery_config", lambda: bad)
        with pytest.raises(ValueError, match="duplicate query_family key"):
            query_families()

    def test_uppercase_family_key_raises(self, monkeypatch):
        bad = {
            "query_families": [
                {"key": "State_Innovation", "description": "bad casing"},
            ]
        }
        monkeypatch.setattr("radar.core.kg.schema.discovery_config", lambda: bad)
        with pytest.raises(ValueError, match="query_family key must be lowercase"):
            query_families()

    def test_empty_families_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr("radar.core.kg.schema.discovery_config", lambda: {})
        assert query_families() == []


# ══════════════════════════════════════════════════════════════════════════
# Backward compatibility — discovery_config
# ══════════════════════════════════════════════════════════════════════════


class TestDiscoveryCompat:

    def test_discovery_config_still_returns_flat_queries(self):
        """O consumidor atual (opportunity_discovery.py) ainda obtém queries planas."""
        cfg = discovery_config()
        queries = cfg.get("queries", [])
        assert isinstance(queries, list)
        assert len(queries) >= 7
        for q in queries:
            assert isinstance(q, str), f"query not a string: {q!r}"

    def test_discovery_config_caps_intact(self):
        cfg = discovery_config()
        assert isinstance(cfg.get("max_results_per_query"), int)
        assert isinstance(cfg.get("max_candidates"), int)
        assert isinstance(cfg.get("max_dou_candidates"), int)

    def test_discovery_config_still_has_discovery_key(self):
        cfg = discovery_config()
        assert "queries" in cfg
        assert "max_results_per_query" in cfg


# ══════════════════════════════════════════════════════════════════════════
# Structural — no parallel lists in code
# ══════════════════════════════════════════════════════════════════════════


class TestNoParallelLists:

    def test_coverage_channels_comes_from_yaml_not_hardcoded(self):
        """O código não mantém lista normativa paralela — lê do YAML."""
        import inspect
        src = inspect.getsource(coverage_channels)
        assert "_coverage.md" not in src  # não referencia o path
        channels = coverage_channels()
        assert len(channels) == 7

    def test_query_families_comes_from_yaml_not_hardcoded(self):
        import inspect
        src = inspect.getsource(query_families)
        # A validação itera sobre o que o YAML devolve, não repete a lista
        assert "state_innovation_funding" not in src
