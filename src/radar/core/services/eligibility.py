"""Avaliador determinístico de elegibilidade dura (KG v2, PR5 / Estágio 0).

Compara as `constraints[]` tipadas de uma Oportunidade (schema D6:
`{tipo, op, valor}`) contra os campos ESTRUTURADOS do perfil da empresa e
devolve um veredito por constraint — `sat` / `unsat` / `unknown` — e o agregado
por oportunidade.

Filosofia (PR5, "unknown não elimina"): perfil incompleto é o estado normal
pré-beta. Um constraint que não pode ser avaliado (campo faltando no perfil)
NUNCA elimina a oportunidade — marca "elegibilidade não verificada" e diz qual
campo completar. Só `unsat` (incompatibilidade COMPROVADA) elimina no filtro
duro do match.

Regras CURADAS (KG v2 resíduos PR-E.2, R4): `data/curadoria/regras_elegibilidade.json`
carrega tabelas de porte (bandas FINEP), contrapartida porte×região e interpretações
padrão (receita, grupo econômico, SUDAM/SUDENE). O avaliador consome essas regras
para expandir/julgar constraints que referenciam portes ou regiões.

Puro/determinístico: sem I/O (fora o load da regra curada), sem LLM.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from radar.core.kg import schema

logger = logging.getLogger(__name__)

SAT = "sat"
UNSAT = "unsat"
UNKNOWN = "unknown"

# Status agregado por oportunidade (o que o card mostra).
ELEGIVEL = "elegivel"
INELEGIVEL = "inelegivel"
NAO_VERIFICADA = "nao_verificada"

# ── normalização de vocabulário (perfil e constraint falam a mesma língua) ────

# Porte é ORDINAL; o produtor expande "até média" para o conjunto {mei,me,epp,media}.
# Aqui só normalizamos as strings para casar (o perfil usa MEI/ME/EPP/MEDIO/GRANDE).
_PORTE_ALIASES = {
    "mei": "mei",
    "me": "me", "micro": "me", "microempresa": "me",
    "epp": "epp", "pequena": "epp", "pequeno": "epp", "pequena empresa": "epp",
    "media": "media", "medio": "media", "média": "media", "médio": "media",
    "media empresa": "media", "média empresa": "media",
    "grande": "grande", "grande empresa": "grande",
}


def _deaccent_lower(s: str) -> str:
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(s).strip().lower())
        if not unicodedata.combining(c)
    )


def _norm_porte(v: Any) -> str:
    key = _deaccent_lower(v)
    return _PORTE_ALIASES.get(key, key)


def _norm_uf(v: Any) -> str:
    return str(v).strip().upper()


# Macro-regiões → UFs. Editais frequentemente restringem por REGIÃO ("Norte/
# Nordeste"), mas o perfil guarda a UF (2 letras). O avaliador expande a região
# para o conjunto de UFs antes de testar pertinência — assim uma constraint
# `sede_uf in [NE]` casa uma empresa em "BA".
_REGIOES = {
    "N": {"AC", "AP", "AM", "PA", "RO", "RR", "TO"},
    "NORTE": {"AC", "AP", "AM", "PA", "RO", "RR", "TO"},
    "NE": {"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"},
    "NORDESTE": {"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"},
    "CO": {"DF", "GO", "MT", "MS"},
    "CENTRO-OESTE": {"DF", "GO", "MT", "MS"},
    # "SE" bare = Sergipe (UF), NÃO Sudeste — evita colisão de sigla; a região
    # Sudeste só expande pela forma escrita por extenso.
    "SUDESTE": {"ES", "MG", "RJ", "SP"},
    "S": {"PR", "RS", "SC"},
    "SUL": {"PR", "RS", "SC"},
}


# Mapa UF → região curta (usa a 1ª chave que contém a UF no _REGIOES,
# priorizando a forma curta N/NE/CO/SUDESTE/S sobre NORTE/NORDESTE etc.).
_UF_REGIAO: dict[str, str] = {}
for _reg, _ufs in _REGIOES.items():
    if _reg in ("NORTE", "NORDESTE", "CENTRO-OESTE", "SUL"):
        continue
    for _uf in _ufs:
        _UF_REGIAO.setdefault(_uf, _reg)


def _expand_uf(values: list) -> set[str]:
    """Expande cada valor de `sede_uf` (UF direta OU macro-região) no conjunto de
    UFs. `SE` é ambíguo (Sergipe × Sudeste): como o edital fala em região quando
    escreve `SE`/`CO`/`NE`, tratamos os tokens de região como região."""
    out: set[str] = set()
    for v in values:
        tok = _norm_uf(v)
        out |= _REGIOES.get(tok, {tok})
    return out


def _norm_forma(v: Any) -> str:
    return _deaccent_lower(v)


def _expand_forma_juridica(values: list) -> set[str]:
    """Expande cada valor EXIGIDO pela sua hierarquia de satisfação
    (`constraint_vocab.satisfies.forma_juridica` no WIKI). Achado do bake-off
    Fase 1.5: exigir `forma_juridica=[empresa]` era avaliado como UNSAT para
    perfis com `tipo_entidade="startup"` — mas toda startup registrada JÁ é
    uma empresa; a ontologia tratava maturidade/estágio e natureza jurídica
    como categorias mutuamente exclusivas. O mapa vive no WIKI (não hardcoded
    aqui) — mudar a hierarquia é editar o doc, não este módulo."""
    satisfies = schema.constraint_vocab_satisfies().get("forma_juridica", {})
    out: set[str] = set()
    for v in values:
        tok = _norm_forma(v)
        out.add(tok)
        out |= {_norm_forma(x) for x in satisfies.get(tok, [])}
    return out


# Mapa tipo-de-constraint → (campo do perfil, normalizador). `parceria` não tem
# campo no perfil (é relacional) → sempre unknown.
_PROFILE_FIELD = {
    "porte": ("tamanho_empresa", _norm_porte),
    "sede_uf": ("uf", _norm_uf),
    "faturamento": ("faturamento_anual", None),
    "trl": ("trl", None),
    "forma_juridica": ("tipo_entidade", _norm_forma),
}


def _profile_get(profile: Any, field: str) -> Any:
    """Lê um campo do perfil aceitando dict OU objeto (CompanyProfile/Pydantic)."""
    if profile is None:
        return None
    if isinstance(profile, dict):
        return profile.get(field)
    return getattr(profile, field, None)


def _as_number(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reason(tipo: str, op: str, valor: Any) -> str:
    """Texto curto e humano do constraint (p/ o card / veredito)."""
    label = {
        "porte": "porte", "sede_uf": "sede (UF)", "faturamento": "faturamento anual",
        "trl": "TRL", "forma_juridica": "natureza jurídica", "parceria": "parceria",
    }.get(tipo, tipo)
    if op == "in":
        return f"{label} deve ser um de {valor}"
    if op == "not_in":
        return f"{label} não pode ser {valor}"
    if op == "lte":
        return f"{label} deve ser ≤ {valor}"
    if op == "gte":
        return f"{label} deve ser ≥ {valor}"
    if op == "exige":
        return f"exige {label} com {valor}"
    return f"{label} {op} {valor}"


def evaluate_constraint(constraint: dict, profile: Any) -> tuple[str, str]:
    """Avalia UM constraint contra o perfil → `(verdict, reason)`.

    `verdict ∈ {sat, unsat, unknown}`. `unknown` quando o tipo é relacional
    (`parceria`) ou o campo correspondente falta no perfil — nunca elimina."""
    tipo = constraint.get("tipo")
    op = constraint.get("op")
    valor = constraint.get("valor")
    reason = _reason(tipo, op, valor)

    # Relacional (parceria/consórcio): o perfil não declara parcerias → unknown.
    if tipo not in _PROFILE_FIELD:
        return UNKNOWN, reason

    field, norm = _PROFILE_FIELD[tipo]
    raw = _profile_get(profile, field)
    if raw in (None, "", []):
        return UNKNOWN, reason

    # Numérico (faturamento/trl): compara por ordem.
    if op in ("lte", "gte"):
        pv, cv = _as_number(raw), _as_number(valor)
        if pv is None or cv is None:
            return UNKNOWN, reason
        ok = pv <= cv if op == "lte" else pv >= cv
        return (SAT if ok else UNSAT), reason

    # Categórico (in/not_in): normaliza os dois lados e testa pertinência.
    values = list(valor) if isinstance(valor, (list, tuple)) else [valor]
    if tipo == "sede_uf":  # expande macro-região → UFs antes de comparar
        pv = _norm_uf(raw)
        vset = _expand_uf(values)
    elif tipo == "forma_juridica":  # expande pela hierarquia de satisfação
        pv = _norm_forma(raw)
        vset = _expand_forma_juridica(values)
    elif norm is not None:
        pv = norm(raw)
        vset = {norm(x) for x in values}
    else:  # trl com lista de valores permitidos, etc.
        pv = _as_number(raw)
        vset = {_as_number(x) for x in values}
    if op == "in":
        return (SAT if pv in vset else UNSAT), reason
    if op == "not_in":
        return (UNSAT if pv in vset else SAT), reason
    return UNKNOWN, reason  # op desconhecido → não arrisca eliminar


def evaluate_opportunity(constraints: list[dict] | None, profile: Any) -> dict:
    """Agrega os constraints de uma Oportunidade contra o perfil.

    Retorna `{status, unsat[], unknown[]}`:
      • `status = inelegivel` se QUALQUER constraint é `unsat` (o filtro duro
        elimina) — `unsat[]` traz os motivos;
      • `status = nao_verificada` se nenhum `unsat` mas há `unknown` — o card
        pede os campos faltantes (`unknown[]`), sem eliminar;
      • `status = elegivel` se todos os avaliáveis são `sat` (ou sem constraints).
    """
    unsat: list[str] = []
    unknown: list[str] = []
    for c in constraints or []:
        verdict, reason = evaluate_constraint(c, profile)
        if verdict == UNSAT:
            unsat.append(reason)
        elif verdict == UNKNOWN:
            unknown.append(reason)

    if unsat:
        status = INELEGIVEL
    elif unknown:
        status = NAO_VERIFICADA
    else:
        status = ELEGIVEL
    return {"status": status, "unsat": unsat, "unknown": unknown}


# ── Regras curadas (KG v2 resíduos PR-E.2, R4) ───────────────────────────────

_CACHED_RULES: dict | None = None


def load_curated_rules() -> dict:
    """Carrega `data/curadoria/regras_elegibilidade.json` (tabelas curadas de
    porte, contrapartida, interpretações). Cacheia em módulo após primeira leitura.
    Retorna dict vazio se o arquivo não existir (fallback seguro)."""
    global _CACHED_RULES
    if _CACHED_RULES is not None:
        return _CACHED_RULES
    path = Path(__file__).resolve().parents[4] / "data" / "curadoria" / "regras_elegibilidade.json"
    if not path.exists():
        logger.warning("regras_elegibilidade.json não encontrado em %s", path)
        _CACHED_RULES = {"version": 1, "portes": {}, "contrapartida": {"tabela": []},
                         "sudam_sudene": {}, "interpretacoes": {}}
        return _CACHED_RULES
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _CACHED_RULES = data
    except Exception as e:
        logger.warning("falha ao carregar regras_elegibilidade.json: %s", e)
        _CACHED_RULES = {"version": 1, "portes": {}, "contrapartida": {"tabela": []},
                         "sudam_sudene": {}, "interpretacoes": {}}
    return _CACHED_RULES


def contrapartida_minima(porte: str, uf: str) -> dict:
    """Percentual de contrapartida mínima esperado dado porte + UF.

    Retorna `{pct: int, regiao: str, fonte: str}` ou dict vazio se não
    houver regra. Usa a expansão de UF→região do `_REGIOES` para casar."""
    rules = load_curated_rules()
    tabela = rules.get("contrapartida", {}).get("tabela", [])
    regiao = _UF_REGIAO.get(uf.upper())
    if not regiao:
        return {}
    # Busca na tabela
    for row in tabela:
        if row.get("porte") == porte and row.get("regiao") == regiao:
            return {"pct": row["contrapartida_pct"], "regiao": regiao, "uf": uf.upper(),
                    "fonte": "regras_elegibilidade.json v1"}
    # Tenta entrada sem região (genérica)
    for row in tabela:
        if row.get("porte") == porte and row.get("regiao") is None:
            return {"pct": row["contrapartida_pct"], "regiao": None, "uf": uf.upper(),
                    "fonte": "regras_elegibilidade.json v1"}
    return {}


def format_curated_rules_block(profile: Any, constraints: list[dict] | None = None) -> str:
    """Formata as regras curadas como bloco de texto para o prompt do veredito.

    Se `constraints` for fornecido, filtra as seções para incluir só os tipos
    de restrição presentes na oportunidade (porte, faturamento, sede_uf...).
    Sem constraints, inclui tudo (fallback)."""
    rules = load_curated_rules()
    lines: list[str] = []

    # Descobre quais seções são relevantes
    tipos: set[str] = set()
    if constraints:
        for c in constraints:
            t = c.get("tipo") if isinstance(c, dict) else None
            if t:
                tipos.add(t)

    # Portes
    portes = rules.get("portes", {})
    if portes and (not tipos or "porte" in tipos or "faturamento" in tipos):
        partes: list[str] = []
        for slug, info in portes.items():
            label = info.get("label", slug)
            fat_max = info.get("faturamento_max")
            if fat_max:
                partes.append(f"{label} ≤ R$ {fat_max:,}")
            else:
                partes.append(f"{label} (sem limite)")
        if partes:
            lines.append("Portes (" + " | ".join(partes) + ")")

    # Contrapartida
    tabela = rules.get("contrapartida", {}).get("tabela", [])
    if tabela:
        p_slug = None
        p_uf = None
        if profile:
            p_slug = _profile_get(profile, "tamanho_empresa")
            p_uf = _profile_get(profile, "uf")
        if p_slug and p_uf:
            cp = contrapartida_minima(p_slug, p_uf)
            if cp:
                lines.append(
                    f"Contrapartida mínima ({p_slug.upper()}, {p_uf.upper()}): "
                    f"{cp['pct']}% (região {cp['regiao']})"
                )
        if not lines or not any("Contrapartida" in ln for ln in lines):
            lines.append("Contrapartida: varia por porte e região (N/NE/CO: reduzida; S/SE: integral)")

    # SUDAM/SUDENE
    sudam = rules.get("sudam_sudene", {})
    if sudam.get("ufs_sudam") and (not tipos or "sede_uf" in tipos):
        lines.append("SUDAM: benefícios fiscais para UFs da Amazônia Legal")

    # Interpretações
    interp = rules.get("interpretacoes", {})
    partes_interp: list[str] = []
    for key in ("receita", "grupo_economico", "data_constituicao"):
        texto = interp.get(key, "")
        if texto:
            texto_curto = texto.split(".")[0].strip() if "." in texto else texto
            partes_interp.append(texto_curto)
    if partes_interp:
        lines.append("Interpretações: " + " | ".join(partes_interp))

    if not lines:
        return ""
    return "\n".join("  • " + ln for ln in lines)
