"""Inferência determinística de campos do perfil a partir do que a extração achou.

Camada separada e desligável do `ProfileExtractor`: a extração diz "o que o site
diz"; aqui inferimos campos que o site quase nunca declara explicitamente.

`infer_financiamento` propõe `tipos_financiamento_interesse` (PROPOSTA — humano
confirma/desmarca no review, "AI drafts, humans decide"). O campo alimenta a
dimensão *mecanismo* do match (peso 15, `_score_mecanismo`) — por isso a mudança é
**eval-gated** (`python -m radar.core.eval matching`).
"""

from __future__ import annotations

import unicodedata

from radar.domain.user_profile import CompanyProfile

# ── Valores válidos ──────────────────────────────────────────────────────────
# Espelham as opções do perfil em frontend/src/components/frontdoor/profileFields.ts.
SUBVENCAO = "subvencao_nao_reembolsavel"
PESQUISA_COLABORATIVA = "pesquisa_colaborativa"
CAPITAL_RISCO = "capital_risco"

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


def _has_investor_signal(profile: CompanyProfile) -> bool:
    """Sinais de que a empresa busca/qualiﬁca para capital privado."""
    if profile.estagio:
        return True
    if profile.mrr_arr is not None and profile.mrr_arr > 0:
        return True
    if profile.round_alvo_brl is not None and profile.round_alvo_brl > 0:
        return True
    blob = _normalize(
        " ".join(
            (profile.one_liner, profile.solution_summary, profile.descricao_atividades),
        ),
    )
    keywords = ("captacao", "rodada", "equity", "venture", "série", "serie a", "investidor")
    return any(kw in blob for kw in keywords)


def infer_financiamento(profile: CompanyProfile) -> list[str]:
    """Infere `tipos_financiamento_interesse` candidatos a partir do perfil.

    PROPOSTA (humano confirma) — alimenta a dimensão *mecanismo* do match
    (peso 15); por isso é eval-gated. 3 opções hoje: subvenção, pesquisa
    colaborativa e capital de risco. Sem sinal técnico, não chuta (`[]` → nota
    neutra no match).
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

    # Capital de risco: startup + sinais de captação ou fields Q3/Q4 preenchidos.
    if entidade == "startup" or _has_investor_signal(profile):
        out.append(CAPITAL_RISCO)

    # Dedup preservando ordem.
    deduped = list(dict.fromkeys(out))

    # Fallback p/ tech sem desambiguação (ex.: tipo_entidade desconhecido).
    return deduped or [SUBVENCAO]
