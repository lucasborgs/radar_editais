"""
Vocabulário canônico de temas.

Stub funcional: faz limpeza básica (strip, lowercase, dedupe preservando ordem).
A canonicalização sofisticada (agrupamento por sinônimo, mapping pra taxonomia)
vive em docs/domain/schema.md como schema autoritativo e pode ser implementada incrementalmente
adicionando o `_SYNONYMS` map abaixo.

Usada por `radar.core.vocab_lint` para normalizar evidências antes de propor alterações
humanas no vocabulário autoritativo.
"""
from __future__ import annotations

# Mapa de sinônimos → canônico (variações do corpus → tema_vocab, docs/domain/schema.md §5.9).
_SYNONYMS: dict[str, str] = {
    # Taxonomia Liferay da FINEP nomeia assim o tema-macro de materiais
    # (auditoria 2026-06-11 das api_taxonomy_categories do bronze).
    "indústria e materiais avançados": "materiais, química e manufatura avançada",
}


def canonicalize_themes(themes_raw) -> list[str]:
    """Normaliza uma lista de temas crus.

    Etapas:
      1. Aceita string única ou lista (None → []).
      2. Strip + lowercase em cada item.
      3. Aplica mapa de sinônimos.
      4. Dedupe preservando ordem.

    Args:
        themes_raw: str | list[str] | None — temas extraídos do bronze.

    Returns:
        list[str] — temas canonicalizados sem duplicatas.
    """
    if not themes_raw:
        return []
    if isinstance(themes_raw, str):
        themes_raw = [themes_raw]

    seen: list[str] = []
    for raw in themes_raw:
        if not raw:
            continue
        normalized = str(raw).strip().lower()
        if not normalized:
            continue
        canonical = _SYNONYMS.get(normalized, normalized)
        if canonical not in seen:
            seen.append(canonical)
    return seen


__all__ = ["canonicalize_themes"]
