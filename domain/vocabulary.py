"""
Vocabulário canônico de temas.

Stub funcional: faz limpeza básica (strip, lowercase, dedupe preservando ordem).
A canonicalização sofisticada (agrupamento por sinônimo, mapping pra taxonomia)
vive em WIKI.md como schema autoritativo e pode ser implementada incrementalmente
adicionando o `_SYNONYMS` map abaixo.

Chamada por `pipeline.build_knowledge_graph._build_editais` no campo `themes`.
"""
from __future__ import annotations

# Mapa de sinônimos → canônico. Vazio por enquanto; preencher conforme
# encontramos variações no corpus (ex: "IA" → "inteligência artificial").
_SYNONYMS: dict[str, str] = {
    # "ia": "inteligência artificial",
    # "machine learning": "inteligência artificial",
    # "energia renovável": "energia limpa",
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
