"""Inferência determinística de campos do perfil a partir do que a extração achou.

Camada separada e desligável do `ProfileExtractor`: a extração diz "o que o site
diz"; aqui inferimos campos que o site quase nunca declara explicitamente.

`infer_financiamento` propõe `tipos_financiamento_interesse` (PROPOSTA — humano
confirma/desmarca no review, "AI drafts, humans decide"). O campo alimenta a
dimensão *mecanismo* do match (peso 15, `_score_mecanismo`) — por isso a mudança é
**eval-gated** (`python -m core.eval matching`).
"""

from __future__ import annotations

import unicodedata

from domain.user_profile import CompanyProfile

# ── Valores válidos de mecanismo (escopo PR #29 — só 2 opções) ────────────────
# Espelham _MECHANISM_MAP em core/services/hybrid_match_service.py.
SUBVENCAO = "subvencao_nao_reembolsavel"
PESQUISA_COLABORATIVA = "pesquisa_colaborativa"

# Termos que sinalizam atividade de P&D/tecnologia no texto livre do perfil.
# Normalizados (sem acento, minúsculos) para comparação. Lista curta e conservadora:
# falso-positivo aqui só vira uma proposta a mais, que o humano desmarca.
_TECH_KEYWORDS: tuple[str, ...] = (
    "pesquisa",
    "p&d",
    "tecnologia",
    "inovacao",
    "desenvolvimento",
    "software",
    "hardware",
    "deep tech",
    "biotec",
    "engenharia",
    "algoritmo",
    "inteligencia artificial",
    # NB: nada de "ia" solto — casa substring em "tecnologia"/"energia"/
    # "consultoria" e dispararia sinal técnico em quase todo texto PT.
)

# tipo_entidade (normalizado) que conta como sinal técnico por si só.
_TECH_ENTITIES = {"startup", "ict"}


def _normalize(text: str) -> str:
    """Minúsculas + remove acentos (NFKD) para comparação robusta."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _has_tech_signal(profile: CompanyProfile) -> bool:
    """True se há sinal técnico/P&D: TRL presente, entidade tech, ou keyword no texto."""
    if profile.trl is not None:
        return True
    if _normalize(profile.tipo_entidade) in _TECH_ENTITIES:
        return True

    blob = _normalize(
        " ".join(
            (
                profile.one_liner,
                profile.solution_summary,
                profile.descricao_atividades,
            ),
        ),
    )
    return any(kw in blob for kw in _TECH_KEYWORDS)


def infer_financiamento(profile: CompanyProfile) -> list[str]:
    """Infere `tipos_financiamento_interesse` candidatos a partir do perfil.

    PROPOSTA (humano confirma) — alimenta a dimensão *mecanismo* do match
    (peso 15); por isso é eval-gated. Só há 2 opções, ambas "fomento competitivo
    por mérito", então a heurística é permissiva mas conservadora: sem sinal
    técnico, não chuta (`[]` → nota neutra no match).
    """
    if not _has_tech_signal(profile):
        return []

    entidade = _normalize(profile.tipo_entidade)
    out: list[str] = []

    # Subvenção: fomento competitivo p/ empresa/startup com produto/tecnologia.
    if entidade in {"empresa", "startup"}:
        out.append(SUBVENCAO)

    # Pesquisa colaborativa: exige ICT/universidade ou P&D early-stage (TRL ≤ 4).
    if entidade in {"universidade", "ict"} or (
        profile.trl is not None and profile.trl <= 4
    ):
        out.append(PESQUISA_COLABORATIVA)

    # Dedup preservando ordem.
    deduped = list(dict.fromkeys(out))

    # Fallback p/ tech sem desambiguação (ex.: tipo_entidade desconhecido).
    return deduped or [SUBVENCAO]
