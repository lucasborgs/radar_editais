"""Testes do registry de canais e famílias de busca (RT03-T01).

Valida:
  - _coverage.md carrega 7 canais com source_key/mode corretos;
  - modos carregados do doc autoritativo (não de lista Python);
  - _discovery.md carrega 4 famílias + queries estruturadas {text, family};
  - invariantes: lowercase, unicidade, modos canônicos, enabled+flag;
  - fixtures inválidas acionam ValueError;
  - compatibilidade: discovery_config() ainda devolve queries planas;
  - código não mantém lista normativa paralela;
  - cada query pertence a exatamente uma família registrada.
"""
from __future__ import annotations

import inspect

import pytest

from radar.core.kg.schema import (
    clear_cache,
    coverage_channel,
    coverage_channels,
    coverage_modes,
    discovery_config,
    discovery_queries,
    discovery_queries_flat,
    query_families,
)


# Helpers de monkeypatch para testes
def _patch_discovery(monkeypatch, data: dict):
    """Patches _discovery_raw (usado por families + queries) e discovery_config."""
    import radar.core.kg.schema as _schema_mod
    monkeypatch.setattr(_schema_mod, "_discovery_raw", lambda: data)
    monkeypatch.setattr(_schema_mod, "discovery_config", lambda: _projected(data))


def _projected(data: dict) -> dict:
    """Simula a projeção que discovery_config faz."""
    cfg = dict(data)
    raw_q = cfg.get("queries", [])
    if raw_q and isinstance(raw_q[0], dict):
        cfg["queries"] = [q["text"] for q in raw_q]
    return cfg

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
# Modes — carregados do doc autoritativo
# ══════════════════════════════════════════════════════════════════════════


class TestModesFromDoc:

    def test_modes_loaded_from_yaml(self):
        modes = coverage_modes()
        assert len(modes) >= 5
        assert "dedicated" in modes
        assert "curated_web" in modes
        assert "open_search" in modes
        assert "official_feed" in modes
        assert "hub" in modes

    def test_no_parallel_list_in_python(self):
        """_VALID_CHANNEL_MODES foi removido; modos vêm exclusivamente do doc."""
        import radar.core.kg.schema as s
        assert not hasattr(s, "_VALID_CHANNEL_MODES")

    def test_invalid_mode_in_channel_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {
                "modes": [{"key": "dedicated"}],
                "channels": [{"source_key": "x", "mode": "nonexistent",
                              "display_name": "X", "scope_note": "test",
                              "expected_interval_hours": 24, "enabled_by_default": True}],
            },
        )
        with pytest.raises(ValueError, match="invalid mode"):
            coverage_channels()

    def test_missing_modes_raises(self, monkeypatch):
        monkeypatch.setattr("radar.core.kg.schema.coverage_config", lambda: {})
        with pytest.raises(ValueError, match="no channel modes found"):
            coverage_modes()


# ══════════════════════════════════════════════════════════════════════════
# Channels — contrato RT03-T01
# ══════════════════════════════════════════════════════════════════════════


class TestChannelsContract:

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

    def test_flag_name_only_for_gated(self):
        gated = {"dou": "DISCOVERY_DOU_ENABLED", "hub_expansion": "DISCOVERY_HUB_CRAWL_ENABLED"}
        for ch in coverage_channels():
            key = ch["source_key"]
            if key in gated:
                assert ch.get("flag_name") == gated[key], f"{key} expected flag_name={gated[key]}"
            else:
                assert "flag_name" not in ch, f"{key} should not have flag_name"

    def test_gated_channels_have_enabled_false(self):
        for ch in coverage_channels():
            if "flag_name" in ch:
                assert ch["enabled_by_default"] is False, (
                    f"{ch['source_key']} with flag_name must have enabled_by_default=false"
                )

    def test_non_gated_channels_have_enabled_true(self):
        for ch in coverage_channels():
            if "flag_name" not in ch:
                if ch["source_key"] not in ("finep", "fapesp", "fapesc", "web_curated", "open_search"):
                    continue
                assert ch["enabled_by_default"] is True, (
                    f"{ch['source_key']} without flag_name must have enabled_by_default=true"
                )

    def test_source_key_lowercase(self):
        for ch in coverage_channels():
            key = ch["source_key"]
            assert key == key.lower(), f"source_key {key!r} is not lowercase"

    def test_open_search_is_logical_not_tavily(self):
        ch = coverage_channel("open_search")
        assert ch is not None
        assert ch["mode"] == "open_search"
        assert "tavily" not in ch.get("scope_note", "").lower()

    def test_lookup_by_key(self):
        ch = coverage_channel("finep")
        assert ch is not None
        assert ch["source_key"] == "finep"
        assert ch["mode"] == "dedicated"

    def test_lookup_missing_returns_none(self):
        assert coverage_channel("nonexistent") is None


# ══════════════════════════════════════════════════════════════════════════
# Channels — field validation
# ══════════════════════════════════════════════════════════════════════════


class TestChannelsFieldValidation:

    _BASE = {"display_name": "X", "scope_note": "x", "expected_interval_hours": 24, "enabled_by_default": True}

    def test_channels_missing_source_key_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {"modes": [{"key": "dedicated"}], "channels": [{"mode": "dedicated", **self._BASE}]},
        )
        with pytest.raises(ValueError, match="missing source_key"):
            coverage_channels()

    def test_channels_missing_display_name_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {"modes": [{"key": "dedicated"}], "channels": [{"source_key": "x", "mode": "dedicated",
                      "scope_note": "x", "expected_interval_hours": 24, "enabled_by_default": True}]},
        )
        with pytest.raises(ValueError, match="missing display_name"):
            coverage_channels()

    def test_channels_missing_scope_note_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {"modes": [{"key": "dedicated"}], "channels": [{"source_key": "x", "mode": "dedicated",
                      "display_name": "X", "expected_interval_hours": 24, "enabled_by_default": True}]},
        )
        with pytest.raises(ValueError, match="missing scope_note"):
            coverage_channels()

    def test_non_positive_interval_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {"modes": [{"key": "dedicated"}], "channels": [{"source_key": "x", "mode": "dedicated",
                      "display_name": "X", "scope_note": "x", "expected_interval_hours": -1, "enabled_by_default": True}]},
        )
        with pytest.raises(ValueError, match="expected_interval_hours must be positive"):
            coverage_channels()

    def test_non_bool_enabled_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {"modes": [{"key": "dedicated"}], "channels": [{"source_key": "x", "mode": "dedicated",
                      "display_name": "X", "scope_note": "x", "expected_interval_hours": 24, "enabled_by_default": "yes"}]},
        )
        with pytest.raises(ValueError, match="enabled_by_default must be boolean"):
            coverage_channels()

    def test_duplicate_source_key_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {
                "modes": [{"key": "dedicated"}],
                "channels": [
                    {"source_key": "finep", "mode": "dedicated", **self._BASE},
                    {"source_key": "finep", "mode": "dedicated", **self._BASE},
                ],
            },
        )
        with pytest.raises(ValueError, match="duplicate source_key"):
            coverage_channels()

    def test_uppercase_source_key_raises(self, monkeypatch):
        monkeypatch.setattr(
            "radar.core.kg.schema.coverage_config",
            lambda: {"modes": [{"key": "dedicated"}], "channels": [{"source_key": "Finep", "mode": "dedicated", **self._BASE}]},
        )
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

    def test_missing_family_key_raises(self, monkeypatch):
        _patch_discovery(monkeypatch, {"query_families": [{"description": "no key"}]})
        with pytest.raises(ValueError, match="missing key"):
            query_families()

    def test_missing_family_description_raises(self, monkeypatch):
        _patch_discovery(monkeypatch, {"query_families": [{"key": "x"}]})
        with pytest.raises(ValueError, match="missing description"):
            query_families()

    def test_duplicate_family_key_raises(self, monkeypatch):
        _patch_discovery(monkeypatch, {"query_families": [
            {"key": "a", "description": "x"},
            {"key": "a", "description": "y"},
        ]})
        with pytest.raises(ValueError, match="duplicate query_family key"):
            query_families()

    def test_uppercase_family_key_raises(self, monkeypatch):
        _patch_discovery(monkeypatch, {"query_families": [{"key": "Foo", "description": "bad"}]})
        with pytest.raises(ValueError, match="query_family key must be lowercase"):
            query_families()

    def test_empty_families_returns_empty_list(self, monkeypatch):
        _patch_discovery(monkeypatch, {})
        assert query_families() == []


# ══════════════════════════════════════════════════════════════════════════
# Structured queries — {text, family} com validação de vínculo
# ══════════════════════════════════════════════════════════════════════════


class TestStructuredQueries:

    def test_discovery_queries_returns_structured(self):
        queries = discovery_queries()
        assert len(queries) >= 7
        for q in queries:
            assert isinstance(q["text"], str)
            assert isinstance(q["family"], str)
            assert q["family"] in ("state_innovation_funding", "corporate_open_innovation",
                                    "startup_acceleration", "international_brazil_access")

    def test_each_query_belongs_to_registered_family(self):
        valid = {fam["key"] for fam in query_families()}
        for q in discovery_queries():
            assert q["family"] in valid, f"query {q['text'][:30]!r} has unregistered family {q['family']!r}"

    def test_queries_with_unknown_family_raises(self, monkeypatch):
        _patch_discovery(monkeypatch, {"query_families": [{"key": "a", "description": "x"}],
                                       "queries": [{"text": "q1", "family": "nonexistent"}]})
        with pytest.raises(ValueError, match="not in registered families"):
            discovery_queries()

    def test_query_missing_text_raises(self, monkeypatch):
        _patch_discovery(monkeypatch, {"query_families": [{"key": "a", "description": "x"}],
                                       "queries": [{"family": "a"}]})
        with pytest.raises(ValueError, match="missing or empty.*text"):
            discovery_queries()

    def test_query_missing_family_raises(self, monkeypatch):
        _patch_discovery(monkeypatch, {"query_families": [{"key": "a", "description": "x"}],
                                       "queries": [{"text": "q1"}]})
        with pytest.raises(ValueError, match="missing or empty.*family"):
            discovery_queries()

    def test_flat_projection(self):
        flat = discovery_queries_flat()
        assert len(flat) >= 7
        for q in flat:
            assert isinstance(q, str)


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
        src = inspect.getsource(coverage_channels)
        assert "_VALID_CHANNEL_MODES" not in src
        assert "coverage_modes" in src or "coverage_config" in src

    def test_modes_not_hardcoded_in_channels(self):
        src = inspect.getsource(coverage_channels)
        assert '"dedicated"' not in src
        assert '"open_search"' not in src

    def test_query_families_not_hardcoded(self):
        src = inspect.getsource(query_families)
        assert "state_innovation_funding" not in src
