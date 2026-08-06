"""Caminhos de inovação por domínio (spec product-pathways-domain-matching.md).

Anotação ADITIVA sobre o funil do `match_v3`: não reescreve ranking nem funil.
Cada entidade do gold é classificada num `tipo` de caminho e recebe o contrato
mínimo `{tipo, entidade, objetivo, requisitos, canal_de_acesso, evidências,
status, proximo_passo}` + uma explicação por domínio (confirmados / inferidos /
pendentes / lacunas / próximo passo).

Princípios da spec:
  - não há score universal entre domínios: crédito, desafio, aceleradora,
    incubadora e ICT não disputam o mesmo número com financiamento público;
  - "unknown" não elimina e afinidade não é promessa de aprovação;
  - investidores nunca são classificados (fora do escopo ativo);
  - ICTs/laboratórios são capacidades/parceiros, nunca "oportunidade".

O módulo é determinístico e puro (sem DB); a fronteira com o banco vive em
`match_v3.find_ict_partners` / routers.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from radar.core.services import eligibility

PATH_TIPO_FINANCIAMENTO = "financiamento"
PATH_TIPO_CREDITO = "credito"
PATH_TIPO_SUBVENCAO = "subvencao"
PATH_TIPO_BOLSA = "bolsa"
PATH_TIPO_DESAFIO = "desafio"
PATH_TIPO_ACELERADORA = "aceleradora"
PATH_TIPO_INCUBADORA = "incubadora"
PATH_TIPO_ICT = "ict"

PATH_TIPOS = (
    PATH_TIPO_FINANCIAMENTO,
    PATH_TIPO_CREDITO,
    PATH_TIPO_SUBVENCAO,
    PATH_TIPO_BOLSA,
    PATH_TIPO_DESAFIO,
    PATH_TIPO_ACELERADORA,
    PATH_TIPO_INCUBADORA,
    PATH_TIPO_ICT,
)

TIPO_LABEL: dict[str, str] = {
    PATH_TIPO_FINANCIAMENTO: "Financiamento público",
    PATH_TIPO_CREDITO: "Crédito",
    PATH_TIPO_SUBVENCAO: "Subvenção",
    PATH_TIPO_BOLSA: "Bolsa",
    PATH_TIPO_DESAFIO: "Desafio / inovação aberta",
    PATH_TIPO_ACELERADORA: "Aceleradora",
    PATH_TIPO_INCUBADORA: "Incubadora",
    PATH_TIPO_ICT: "ICT / laboratório",
}

TIPO_CRITERIA: dict[str, str] = {
    PATH_TIPO_FINANCIAMENTO: "elegibilidade, instrumento, projeto, prazo e contrapartida",
    PATH_TIPO_CREDITO: "finalidade, maturidade financeira, garantias e pagamento",
    PATH_TIPO_SUBVENCAO: "elegibilidade, instrumento, projeto, prazo e contrapartida",
    PATH_TIPO_BOLSA: "elegibilidade, modalidade da bolsa, prazo e requisitos do candidato",
    PATH_TIPO_DESAFIO: "problema, solução, estágio e formato de participação",
    PATH_TIPO_ACELERADORA: "estágio, suporte, contrapartida e programa",
    PATH_TIPO_INCUBADORA: "estágio, suporte, contrapartida e programa",
    PATH_TIPO_ICT: "competência, equipamento, projeto, localização e acesso",
}

TIPO_NEXT_STEP: dict[str, str] = {
    PATH_TIPO_FINANCIAMENTO: (
        "Estruture o plano de trabalho e a proposta técnica/contrapartida antes do prazo."
    ),
    PATH_TIPO_CREDITO: (
        "Consulte o agente financeiro sobre condições, garantias e capacidade de pagamento."
    ),
    PATH_TIPO_SUBVENCAO: (
        "Estruture o plano de trabalho e a proposta técnica/contrapartida antes do prazo."
    ),
    PATH_TIPO_BOLSA: (
        "Verifique a modalidade da bolsa, os requisitos do candidato e o prazo de candidatura."
    ),
    PATH_TIPO_DESAFIO: (
        "Inscreva-se no desafio seguindo o formato de participação do promotor."
    ),
    PATH_TIPO_ACELERADORA: (
        "Candidate-se ao programa com o pitch/deck e o estágio solicitados."
    ),
    PATH_TIPO_INCUBADORA: (
        "Candidate-se à incubadora com pitch e plano de negócio."
    ),
    PATH_TIPO_ICT: (
        "Entre em contato para propor uma parceria de P&D e verificar condições de acesso."
    ),
}

_PROJECT_FIELDS = ("portfolio_projetos", "solution_summary", "one_liner")

# Sinais de texto (normalizados, sem acento) por tipo. Conservadores: só uma
# frase inequívoca muda a classificação; o default é financiamento público.
_CREDITO_MARKERS = ("reembols", "linha de credito", "credito de inovacao")
_CREDITO_NEGATORS = ("nao reembols", "sem reembols")
_SUBVENCAO_MARKERS = ("subvencao", "nao reembols", "sem reembols", "nao-reembols")
_BOLSA_MARKERS = ("bolsa de pesquisa", "bolsas de pesquisa", " bolsa ", " bolsas ")
_DESAFIO_MARKERS = (
    "desafio de inovacao", "desafio corporativo", "desafio publico",
    "desafio de startups", "programa de desafio", "desafio aberto",
    "inovacao aberta", "open innovation", "hackathon",
)


def _norm(text: str) -> str:
    """Deburr + lowercase + colapsa espaços (mesma normalização da célula boost)."""
    s = unicodedata.normalize("NFKD", text or "")
    s = s.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.lower()).strip()


def _clip(text: str, limit: int = 240) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _profile_get(profile: Any, field: str) -> Any:
    if profile is None:
        return None
    if hasattr(profile, "model_dump"):  # pydantic
        return (profile.model_dump() or {}).get(field)
    if isinstance(profile, dict):
        return profile.get(field)
    return getattr(profile, field, None)  # CompanyProfile (dataclass)


def has_project(profile: Any) -> bool:
    """Projeto definido ≠ intenção: basta um dos campos de projeto não vazio."""
    return any(str(_profile_get(profile, f) or "").strip() for f in _PROJECT_FIELDS)


def classify_tipo(e: dict) -> str | None:
    """Tipo de caminho da entidade gold (determinístico).

    Nunca retorna tipo para investidores (fora do escopo ativo). Editais sem
    sinal de crédito/desafio são financiamento público (default). Programas são
    classificados por `metadata.tipo` (schema 6.1.4) com fallback de texto.
    """
    kind = e.get("kind") or ""
    if kind == "investidor":
        return None
    if kind == "ict":
        return PATH_TIPO_ICT

    meta = e.get("metadata") or {}
    tipo = _norm(meta.get("tipo"))
    formato = _norm(meta.get("formato"))
    text = _norm(" ".join(str(x) for x in (
        e.get("description"), meta.get("tipo"), meta.get("instrumento"),
        meta.get("formato"), e.get("requisitos_texto"),
    ) if x))

    if kind == "programa":
        if "acelerac" in tipo or "acelerac" in text:
            return PATH_TIPO_ACELERADORA
        if "incubac" in tipo or "incubac" in text:
            return PATH_TIPO_INCUBADORA
        if "subvencao" in tipo or any(m in text for m in _SUBVENCAO_MARKERS):
            return PATH_TIPO_SUBVENCAO
        if "bolsa" in tipo or any(m in text for m in _BOLSA_MARKERS):
            return PATH_TIPO_BOLSA
        if any(t in tipo for t in ("fundo", "capacitac")):
            return PATH_TIPO_FINANCIAMENTO
        if any(m in text for m in _DESAFIO_MARKERS):
            return PATH_TIPO_DESAFIO
        return PATH_TIPO_FINANCIAMENTO

    # edital (e qualquer outro kind ativo)
    if any(m in text for m in _CREDITO_MARKERS) and not any(
        n in text for n in _CREDITO_NEGATORS
    ):
        return PATH_TIPO_CREDITO
    if "desafio" in formato or any(m in text for m in _DESAFIO_MARKERS):
        return PATH_TIPO_DESAFIO
    if "subvencao" in tipo or any(m in text for m in _SUBVENCAO_MARKERS):
        return PATH_TIPO_SUBVENCAO
    if "bolsa" in tipo or any(m in text for m in _BOLSA_MARKERS):
        return PATH_TIPO_BOLSA
    return PATH_TIPO_FINANCIAMENTO


def _project_text(profile: Any) -> str:
    return next(
        (str(_profile_get(profile, f) or "").strip() for f in _PROJECT_FIELDS
         if str(_profile_get(profile, f) or "").strip()),
        "",
    )


def _requisitos(e: dict) -> list[str]:
    raw = e.get("requisitos_texto") or []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in raw:
        s = str(item).strip()
        if s:
            out.append(_clip(s, 240))
        if len(out) >= 5:
            break
    return out


def _objetivo(e: dict, profile: Any) -> str:
    offer = _clip((e.get("description") or "").strip() or (e.get("name") or ""), 200)
    proj = _project_text(profile)
    if proj:
        return f"{offer} — foco: {_clip(proj, 120)}"[: 240]
    return offer


def _canal_de_acesso(e: dict, url: str | None) -> str:
    meta = e.get("metadata") or {}
    return _clip(url or str(meta.get("site") or meta.get("url") or meta.get("faq_url") or ""), 240)


def _evidencias(shared_themes: set[str], excerpts: list[dict]) -> list[dict]:
    out: list[dict] = []
    if shared_themes:
        out.append({
            "tipo": "tema",
            "detalhe": "Temas em comum: " + ", ".join(sorted(shared_themes)[:5]),
        })
    for x in excerpts[:3]:
        company = _clip(x.get("company_text"), 200)
        edital = _clip(x.get("edital_text"), 200)
        if company or edital:
            out.append({
                "tipo": "trecho",
                "empresa": company,
                "oportunidade": edital,
            })
    return out


def _path_status(tipo: str, eleg: dict | None, has_project: bool) -> str:
    if tipo == PATH_TIPO_ICT:
        return "possibilidade"  # parceria; sem contrato de elegibilidade
    if not has_project:
        # Aceite 3: intenção sem projeto é hipótese revisável — nunca declara
        # candidatura viável mesmo quando as constraints avaliáveis satisfazem.
        return "possibilidade"
    if eleg is None:
        return "possibilidade"
    if eleg.get("status") == eligibility.ELEGIVEL:
        return "candidatura_viável"
    return "lacunas"  # nao_verificada (unknown) ou (teoricamente) inelegivel


def build_path(
    e: dict,
    *,
    profile: Any,
    eleg: dict | None,
    url: str | None = None,
    shared_themes: set[str] | None = None,
    excerpts: list[dict] | None = None,
) -> dict | None:
    """Contrato mínimo do caminho — ver spec §2. `None` para investidores."""
    tipo = classify_tipo(e)
    if tipo is None:
        return None
    return {
        "tipo": tipo,
        "entidade": e.get("native_id") or "",
        "objetivo": _objetivo(e, profile),
        "requisitos": _requisitos(e),
        "canal_de_acesso": _canal_de_acesso(e, url),
        "evidencias": _evidencias(shared_themes or set(), excerpts or []),
        "status": _path_status(tipo, eleg, has_project(profile)),
        "proximo_passo": TIPO_NEXT_STEP[tipo],
    }


def build_explanation(
    tipo: str | None,
    *,
    e: dict,
    eleg: dict | None,
    profile: Any,
    has_project: bool,
    shared_themes: set[str] | None = None,
) -> dict | None:
    """Explicação por domínio: fatos confirmados / inferências / pendentes /
    lacunas / próximo passo. `None` para investidores."""
    if tipo is None:
        return None
    confirmados: list[str] = []
    inferidos: list[str] = []
    pendentes: list[str] = []
    lacunas: list[str] = []

    if shared_themes:
        confirmados.append("Temas em comum com o perfil: " + ", ".join(sorted(shared_themes)[:5]) + ".")
    if e.get("status"):
        confirmados.append(f"Oferta catalogada como {e['status']}.")

    if tipo == PATH_TIPO_ICT:
        cap = e.get("capacidades") or {}
        if cap.get("institution"):
            confirmados.append(f"Instituição declarada pela fonte: {_clip(str(cap['institution']), 120)}.")
        if cap.get("municipio"):
            confirmados.append(f"Localização declarada pela fonte: {_clip(str(cap['municipio']), 120)}.")
        competencies = [str(c) for c in (cap.get("competencias") or []) if str(c).strip()]
        if competencies:
            confirmados.append("Competências declaradas pela fonte: " + ", ".join(competencies[:5]) + ".")
        equipments = [str(eq) for eq in (cap.get("equipamentos") or []) if str(eq).strip()]
        if equipments:
            confirmados.append("Equipamentos declarados pela fonte: " + ", ".join(equipments[:5]) + ".")
        if cap.get("condicoes_acesso"):
            confirmados.append(
                f"Condições de acesso declaradas pela fonte: {_clip(str(cap['condicoes_acesso']), 140)}."
            )
        if cap.get("verificado_em"):
            confirmados.append(f"Capacidade catalogada com verificação em {cap['verificado_em']}.")
        inferidos.append(
            "Capacidade candidata por tema compartilhado — validar competência, "
            "equipamento e condições de acesso diretamente com a ICT."
        )
        pendentes.append("Condições de acesso, contato e localização.")
    else:
        if eleg is not None:
            for c in eleg.get("unknown") or []:
                pendentes.append(c)
            for c in eleg.get("unsat") or []:
                lacunas.append(c)
            if eleg.get("status") == eligibility.ELEGIVEL and has_project:
                confirmados.append("Constraints de elegibilidade avaliáveis satisfeitos.")
            else:
                inferidos.append(
                    "Afinidade de escopo via similaridade; não é promessa de aprovação."
                )
        else:
            inferidos.append("Afinidade de escopo via similaridade; não é promessa de aprovação.")

    if not has_project:
        pendentes.append("Defina o projeto/hipótese de escopo para validar o encaixe.")
        inferidos.append(
            "Caminho retornado a partir da intenção — nenhuma elegibilidade declarada."
        )

    return {
        "tipo": tipo,
        "dominio": TIPO_LABEL[tipo],
        "criterios": TIPO_CRITERIA[tipo],
        "confirmados": confirmados,
        "inferidos": inferidos,
        "pendentes": pendentes,
        "lacunas": lacunas,
        "proximo_passo": TIPO_NEXT_STEP[tipo],
    }
