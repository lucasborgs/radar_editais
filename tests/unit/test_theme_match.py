"""`_theme_match` — filtro de tema do catálogo (entity_catalog).

Entidade exclusivamente 'Multissetorial' (programas/oportunidades setor-agnósticos,
fallback do gold) deve casar QUALQUER tema — essa é a correção da lacuna de
programas no Explorer. Os demais casos preservam o comportamento histórico.
"""

from radar.core.kg.entity_catalog import _theme_match


def test_empty_needle_matches_any_themes():
    assert _theme_match("", ["Agro"]) is True
    assert _theme_match("  ", ["Multissetorial"]) is True
    assert _theme_match("", []) is True


def test_multissetorial_matches_any_theme():
    assert _theme_match("florestal", ["Multissetorial"]) is True
    assert _theme_match("agro", ["Multissetorial"]) is True
    assert _theme_match("saude", ["Multissetorial"]) is True
    assert _theme_match("energia", ["Multissetorial"]) is True


def test_multissetorial_aliases_match_any_theme():
    assert _theme_match("florestal", ["Multissetoriais"]) is True
    assert _theme_match("florestal", ["transversal"]) is True


def test_empty_themes_never_match_concrete_needle():
    assert _theme_match("florestal", []) is False


def test_sector_specific_themes_do_not_match_any_theme():
    assert _theme_match("florestal", ["Social", "Sustentabilidade"]) is False


def test_substring_match_preserved():
    assert _theme_match("agro", ["Agro", "Bioeconomia"]) is True
    assert _theme_match("sustentabilidade", ["Sustentabilidade"]) is True


def test_token_match_preserved():
    assert _theme_match("bioenergia", ["Agro", "Energia"]) is True


def test_mixed_with_multissetorial_falls_back_to_normal():
    assert _theme_match("florestal", ["Agro"]) is False
    assert _theme_match("saude", ["Energia"]) is False
