"""
HybridMatchService — Matching em dois estágios.

Stage 1 (determinístico): compara campos estruturados da wiki page com o CompanyProfile.
  - Pontuação por dimensão (100 pts total)
  - Elimina incompatíveis antes de chamar a LLM

Stage 2 (semântico): LLM avalia alinhamento temático para os editais elegíveis.
  - Recebe apenas os editais que passaram no Stage 1
  - Usa descricao_atividades + portfolio_projetos (texto livre)
  - Retorna justificativa por edital
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date

from core.kg import kg_store
from core.kg.edital_id import iter_wiki_pages
from core.kg.wiki_schema import parse_deadline
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

# =============================================================================
# MAPEAMENTOS PARA STAGE 1
# =============================================================================

# tipo_entidade do perfil → labels aceitos em eligible_entities / publico_alvo do card
_ENTITY_MAP: dict[str, set[str]] = {
    "empresa":      {"empresas", "empresa", "startups", "startup"},
    "startup":      {"startups", "startup", "empresas", "empresa"},
    "ict":          {"icts", "ict", "instituições de pesquisa", "institutos", "universidades"},
    "universidade": {"universidades", "universidade", "icts", "ict", "instituições de pesquisa"},
}

# tamanho_empresa → labels de publico_alvo compatíveis
_PORTE_MAP: dict[str, set[str]] = {
    "MEI":    {"empresas", "microempresas", "mei"},
    "ME":     {"empresas", "microempresas", "pequenas empresas"},
    "EPP":    {"empresas", "pequenas empresas", "médias empresas"},
    "MEDIO":  {"empresas", "médias empresas", "grandes empresas"},
    "GRANDE": {"empresas", "grandes empresas"},
}

# Portes com capacidade de contrapartida financeira
_PORTE_CONTRAPARTIDA_OK = {"MEDIO", "GRANDE"}
_PORTE_CONTRAPARTIDA_PARCIAL = {"EPP"}

# tipos_financiamento_interesse → mechanism do card
_MECHANISM_MAP: dict[str, set[str]] = {
    "subvencao_nao_reembolsavel": {"subvencao", "misto"},
    "credito_reembolsavel":       {"reembolsavel", "misto"},
    "investimento_direto":        {"investimento", "misto"},
    "pesquisa_colaborativa":      {"subvencao", "misto"},
    "matching_embrapii":          {"investimento", "misto"},
}

# Palavras irrelevantes para matching temático
_STOP_WORDS = {
    "de", "da", "do", "das", "dos", "em", "na", "no", "nas", "nos",
    "para", "com", "por", "que", "uma", "como", "seus", "suas", "seu",
    "mais", "entre", "sobre", "também", "pela", "pelo", "pelas", "pelos",
}

# Pesos das dimensões — fallback hardcoded usado se DB indisponível ou se a
# tabela matching_weights ainda não foi criada (migration 004 não aplicada).
# As 5 primeiras somam 100 (contrato pré-existente). `elegibilidade_dura` é uma
# dimensão CONDICIONAL: só entra no breakdown quando o card declara
# `eligibility_constraints` (região/idade/faturamento). Hoje os cards de prod não
# carregam esse campo → a dimensão fica dormente e não altera o ranking atual; ela
# liga sozinha quando o extrator v2 popular o campo no card pipeline.
_WEIGHTS = {
    "elegibilidade":    30,
    "tematico":         25,
    "trl":              20,
    "mecanismo":        15,
    "contrapartida":    10,
    "elegibilidade_dura": 10,
}

# Editais abaixo desse score no Stage 1 são eliminados
_ELIMINATION_THRESHOLD = 25


# =============================================================================
# CACHE DE PESOS (ADR A5)
# =============================================================================
# In-memory TTL cache para evitar bater no Postgres a cada match.
# Chave: workspace_id (str UUID) ou "__global__" quando workspace_id é None.
# Valor: (timestamp_monotonic_segundos, dict de pesos).

_WEIGHTS_CACHE_TTL_SECONDS = 60.0
_weights_cache: dict[str, tuple[float, dict[str, float]]] = {}


def _cache_key(workspace_id: str | None) -> str:
    return workspace_id or "__global__"


def get_weights(workspace_id: str | None = None) -> dict[str, float]:
    """Lê pesos da tabela matching_weights, com cache TTL de 60s.

    Lógica de merge: começa com pesos globais (workspace_id IS NULL) e
    sobrepõe com pesos específicos do workspace quando existirem (overrides
    têm prioridade).

    Em qualquer falha (DB indisponível, tabela inexistente antes da migration
    004, RLS, etc.) cai no fallback `_WEIGHTS` hardcoded — matching nunca
    deve quebrar por ausência de configuração.
    """
    key = _cache_key(workspace_id)
    now = time.monotonic()
    cached = _weights_cache.get(key)
    if cached is not None and (now - cached[0]) < _WEIGHTS_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        # Service-role: leitura não-sensível, e RLS exigiria o JWT do request
        # — o que tornaria a função difícil de chamar de pipelines/jobs.
        from core.db import get_supabase_service
        client = get_supabase_service()

        # Pesos globais (workspace_id IS NULL)
        global_rows = (
            client.table("matching_weights")
            .select("dimension, weight")
            .is_("workspace_id", "null")
            .execute()
        )
        merged: dict[str, float] = {
            row["dimension"]: float(row["weight"])
            for row in (global_rows.data or [])
        }

        # Overrides do workspace específico
        if workspace_id:
            ws_rows = (
                client.table("matching_weights")
                .select("dimension, weight")
                .eq("workspace_id", workspace_id)
                .execute()
            )
            for row in (ws_rows.data or []):
                merged[row["dimension"]] = float(row["weight"])

        if not merged:
            # DB conectou mas não há rows globais — usa fallback para não
            # devolver dict vazio (cenário esperado se a migration 004 rodou
            # parcialmente sem seed).
            merged = {k: float(v) for k, v in _WEIGHTS.items()}

        _weights_cache[key] = (now, merged)
        return merged

    except Exception as e:
        # Fallback gracioso: tabela não existe, conexão caiu, etc.
        # Log em DEBUG porque é esperado em ambientes sem Supabase (testes).
        logger.debug("get_weights: fallback para _WEIGHTS (%s)", e)
        fallback = {k: float(v) for k, v in _WEIGHTS.items()}
        # Cacheia o fallback também para evitar retentativas em loop.
        _weights_cache[key] = (now, fallback)
        return fallback


# =============================================================================
# STAGE 1 — SCORING DETERMINÍSTICO
# =============================================================================

@dataclass
class Stage1Result:
    edital_id: str
    score: int                      # 0–100
    breakdown: dict[str, int]       # pontos por dimensão
    eligible: bool                  # passou do threshold
    card: dict                      # card ou entry do índice


def _normalize(text: str) -> str:
    return text.lower().strip()


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _keywords(text: str) -> set[str]:
    """Extrai palavras significativas (>4 chars, sem stop words, sem acentos)."""
    words = _strip_accents(_normalize(text)).split()
    return {w for w in words if len(w) > 4 and w not in _STOP_WORDS}


def _w(weights: dict[str, float], dim: str) -> float:
    """Lê peso, caindo no fallback hardcoded se a dimensão estiver ausente."""
    return float(weights.get(dim, _WEIGHTS[dim]))


def _score_elegibilidade(
    card: dict, profile: CompanyProfile, weights: dict[str, float]
) -> float:
    """Verifica se o tipo de entidade e porte da empresa se encaixam no edital."""
    w = _w(weights, "elegibilidade")
    eligible = {
        _normalize(e)
        for e in card.get("eligible_entities", []) + card.get("publico_alvo", [])
    }
    if not eligible:
        return w / 2  # sem info → neutro

    entity_labels = _ENTITY_MAP.get(_normalize(profile.tipo_entidade), set())
    porte_labels = _PORTE_MAP.get(profile.tamanho_empresa or "", set())
    all_labels = entity_labels | porte_labels

    if all_labels & eligible:
        return w

    # Fallback: se "empresas" está no edital e temos qualquer empresa
    if profile.tipo_entidade in ("empresa", "startup") and "empresas" in eligible:
        return w

    return 0


def _score_tematico(
    card: dict, profile: CompanyProfile, weights: dict[str, float]
) -> float:
    """Interseção de keywords do perfil com themes/eligible_sectors do edital."""
    w = _w(weights, "tematico")
    edital_themes = (card.get("themes") or []) + (card.get("eligible_sectors") or [])
    if not edital_themes:
        return w / 2  # sem info → neutro

    edital_kw = set()
    for theme in edital_themes:
        edital_kw |= _keywords(theme)

    profile_text = " ".join(filter(None, [
        profile.one_liner,
        profile.solution_summary,
        profile.descricao_atividades[:600] if profile.descricao_atividades else "",
    ]))
    if not profile_text.strip():
        return w / 2

    profile_kw = _keywords(profile_text)

    if not edital_kw:
        return w / 2

    overlap = len(edital_kw & profile_kw)
    # Normaliza pelo número de keywords do edital (máx 5 para evitar inflação)
    coverage = min(overlap / min(len(edital_kw), 5), 1.0)
    return w * coverage


def _score_trl(
    card: dict, profile: CompanyProfile, weights: dict[str, float]
) -> float:
    w = _w(weights, "trl")
    trl_range = card.get("trl_range") or {}
    trl_min = trl_range.get("min")
    trl_max = trl_range.get("max")

    if trl_min is None and trl_max is None:
        return w / 2  # sem info → neutro

    if profile.trl is None:
        return w / 2

    trl_min = trl_min or 1
    trl_max = trl_max or 9

    if trl_min <= profile.trl <= trl_max:
        return w

    # Parcial: 1 nível de distância
    if abs(profile.trl - trl_min) == 1 or abs(profile.trl - trl_max) == 1:
        return w / 2

    return 0


def _score_mecanismo(
    card: dict, profile: CompanyProfile, weights: dict[str, float]
) -> float:
    w = _w(weights, "mecanismo")
    card_mechanism = _normalize(card.get("mechanism") or "")
    if not card_mechanism:
        return w / 2  # sem info → neutro

    if not profile.tipos_financiamento_interesse:
        return w / 2

    for interesse in profile.tipos_financiamento_interesse:
        accepted = _MECHANISM_MAP.get(_normalize(interesse), set())
        if card_mechanism in accepted:
            return w

    return 0


def _score_contrapartida(
    card: dict, profile: CompanyProfile, weights: dict[str, float]
) -> float:
    """Avalia se a empresa tem capacidade de arcar com contrapartida quando exigida."""
    w = _w(weights, "contrapartida")
    counterpart_required = card.get("counterpart_required")

    # Edital não exige contrapartida → ponto cheio
    if not counterpart_required:
        return w

    # Exige contrapartida: verifica porte/capital
    porte = profile.tamanho_empresa or ""
    if porte in _PORTE_CONTRAPARTIDA_OK:
        return w

    if porte in _PORTE_CONTRAPARTIDA_PARCIAL:
        return w / 2

    # MEI/ME: capital social pode salvar se suficientemente alto
    if profile.capital_social and profile.capital_social >= 500_000:
        return w / 2

    if porte in ("MEI", "ME"):
        return 0

    # Sem info de porte → neutro
    return w / 2


# --- Elegibilidade dura (dimensão CONDICIONAL): região / idade / faturamento ---
# Pares perfil↔edital dos critérios organizacionais que os editais filtram. A
# dimensão só entra no scoring quando o card declara `eligibility_constraints`.

_UF_REGIAO: dict[str, str] = {
    "AC": "norte", "AP": "norte", "AM": "norte", "PA": "norte", "RO": "norte",
    "RR": "norte", "TO": "norte",
    "AL": "nordeste", "BA": "nordeste", "CE": "nordeste", "MA": "nordeste",
    "PB": "nordeste", "PE": "nordeste", "PI": "nordeste", "RN": "nordeste",
    "SE": "nordeste",
    "DF": "centro-oeste", "GO": "centro-oeste", "MT": "centro-oeste", "MS": "centro-oeste",
    "ES": "sudeste", "MG": "sudeste", "RJ": "sudeste", "SP": "sudeste",
    "PR": "sul", "RS": "sul", "SC": "sul",
}

_UF_NOME: dict[str, str] = {
    "AC": "acre", "AL": "alagoas", "AP": "amapa", "AM": "amazonas", "BA": "bahia",
    "CE": "ceara", "DF": "distrito federal", "ES": "espirito santo", "GO": "goias",
    "MA": "maranhao", "MT": "mato grosso", "MS": "mato grosso do sul", "MG": "minas gerais",
    "PA": "para", "PB": "paraiba", "PR": "parana", "PE": "pernambuco", "PI": "piaui",
    "RJ": "rio de janeiro", "RN": "rio grande do norte", "RS": "rio grande do sul",
    "RO": "rondonia", "RR": "roraima", "SC": "santa catarina", "SP": "sao paulo",
    "SE": "sergipe", "TO": "tocantins",
}

# Tipos de constraint que sabemos casar com um campo-par do perfil. Outros tipos
# (cnae, consortium, …) são ignorados — não contam para o máximo da dimensão.
_CONSTRAINT_TIPOS_SUPORTADOS = {"region", "company_age", "revenue"}


def _constraint_text(c: dict) -> str:
    """Texto normalizado (sem acento, minúsculo) de uma constraint p/ casamento."""
    return _strip_accents(_normalize(
        " ".join(filter(None, [c.get("description") or "", c.get("evidence") or ""]))
    ))


def _score_region(c: dict, profile: CompanyProfile) -> float | None:
    """1.0 se a UF do perfil é elegível, 0.0 se diverge, None se perfil sem UF."""
    uf = (profile.uf or "").strip().upper()
    if not uf or uf not in _UF_REGIAO:
        return None  # perfil sem o par → caller trata como neutro
    text = _constraint_text(c)
    if not text:
        return None
    # Casa por sigla (token), nome do estado, ou macro-região da UF.
    sigla = uf.lower()
    tokens = set(re.findall(r"[a-z]+", text))
    if sigla in tokens or _UF_NOME.get(uf, "###") in text or _UF_REGIAO[uf] in text:
        return 1.0
    return 0.0


def _first_number(text: str) -> float | None:
    """Primeiro número do texto (aceita 1.234,56 / 4,8 / 5). None se nenhum."""
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    if not m:
        return None
    raw = m.group(0).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _score_company_age(c: dict, profile: CompanyProfile) -> float | None:
    """1.0 se a idade da empresa respeita o limite da constraint, 0.0 se excede."""
    if profile.ano_fundacao is None:
        return None
    text = _constraint_text(c)
    limite = _first_number(text)
    if limite is None:
        return None
    idade = date.today().year - int(profile.ano_fundacao)
    # Constraints de idade são tetos no domínio ("até X anos de constituição").
    return 1.0 if idade <= limite else 0.0


def _score_revenue(c: dict, profile: CompanyProfile) -> float | None:
    """1.0 se o faturamento respeita o teto da constraint, 0.0 se excede."""
    if profile.faturamento_anual is None:
        return None
    text = _constraint_text(c)
    teto = _first_number(text)
    if teto is None:
        return None
    # Heurística de escala: "milhão/milhões" multiplica o número-base.
    if "milh" in text:
        teto *= 1_000_000
    elif "mil" in text:
        teto *= 1_000
    return 1.0 if profile.faturamento_anual <= teto else 0.0


_CONSTRAINT_SCORERS = {
    "region": _score_region,
    "company_age": _score_company_age,
    "revenue": _score_revenue,
}


def _score_elegibilidade_dura(
    card: dict, profile: CompanyProfile, weights: dict[str, float]
) -> float | None:
    """Dimensão soft sobre `eligibility_constraints` (região/idade/faturamento).

    Retorna None quando a dimensão NÃO se aplica ao card (sem constraints, ou só
    tipos não-suportados) — nesse caso o caller a omite do breakdown (dormência).
    Quando se aplica, agrega as sub-pontuações ∈ [0,1] e escala pelo peso:
      match → 1.0 · w ; mismatch → 0.0 ; constraint presente mas perfil sem o par
      → 0.5 (sinal HITL). NUNCA elimina o edital (soft) — só re-rankeia.
    """
    constraints = card.get("eligibility_constraints") or []
    sub: list[float] = []
    for c in constraints:
        if not isinstance(c, dict):
            continue
        scorer = _CONSTRAINT_SCORERS.get((c.get("type") or "").lower())
        if scorer is None:
            continue  # tipo não-suportado → não conta para o máximo
        res = scorer(c, profile)
        sub.append(0.5 if res is None else res)  # None = perfil sem o par → neutro
    if not sub:
        return None
    w = _w(weights, "elegibilidade_dura")
    return w * (sum(sub) / len(sub))


def score_stage1(
    edital: dict,
    profile: CompanyProfile,
    weights: dict[str, float] | None = None,
) -> Stage1Result:
    """Pontua um edital contra o perfil de empresa (Stage 1 determinístico).

    `weights` é opcional para compatibilidade retroativa; se None, usa o
    fallback hardcoded `_WEIGHTS`.
    """
    w = weights if weights is not None else {k: float(v) for k, v in _WEIGHTS.items()}
    breakdown_float = {
        "elegibilidade": _score_elegibilidade(edital, profile, w),
        "tematico":      _score_tematico(edital, profile, w),
        "trl":           _score_trl(edital, profile, w),
        "mecanismo":     _score_mecanismo(edital, profile, w),
        "contrapartida": _score_contrapartida(edital, profile, w),
    }
    # Dimensão CONDICIONAL: só entra quando o card declara eligibility_constraints
    # (região/idade/faturamento). Card sem o campo → None → omitida (dormente,
    # ranking idêntico ao legado). Liga sozinha quando o extrator v2 popular o card.
    elig_dura = _score_elegibilidade_dura(edital, profile, w)
    if elig_dura is not None:
        breakdown_float["elegibilidade_dura"] = elig_dura

    # Mantém contrato pré-existente: breakdown em ints arredondados.
    breakdown = {k: round(v) for k, v in breakdown_float.items()}
    total = round(sum(breakdown_float.values()))

    return Stage1Result(
        edital_id=edital["id"],
        score=total,
        breakdown=breakdown,
        eligible=total >= _ELIMINATION_THRESHOLD,
        card=edital,
    )


# =============================================================================
# STAGE 2 — LLM SEMÂNTICO
# =============================================================================

# Stage 2 é dividido em duas chamadas com cardinalidade e consumidor distintos:
#   2a SCORING  — só {id: score} de TODOS os elegíveis. Output minúsculo e
#                 limitado → nunca trunca, mesmo com catálogo grande. É o que o
#                 ranking precisa.
#   2b EXPLICAÇÃO — justificativa/dimensões SÓ do top-K exibido. Output limitado
#                 por K (não pelo tamanho do catálogo). É o que a UI mostra.
# Antes uma única chamada gerava prosa de todos os editais → estourava
# max_tokens e o JSON truncado caía num `return {}` silencioso (score 5.0 flat).

_STAGE2_SCORE_SYSTEM = """Você é um especialista em fomento à inovação no Brasil.
Avalie o alinhamento temático entre o perfil de uma empresa e editais de fomento
que já passaram por um filtro estrutural. Foque apenas na adequação temática e
setorial. Responda APENAS com JSON válido."""

_STAGE2_SCORE_USER = """PERFIL DA EMPRESA:
{profile_context}

{temporal_block}EDITAIS PARA AVALIAÇÃO TEMÁTICA:
{editais_json}

Para cada edital, dê uma pontuação temática de 0.0 a 10.0. Considere: área de
atuação da empresa vs temas/setores do edital, experiência prévia relevante e
aderência do problema/solução ao foco do programa.

Responda SÓ com o mapa id→score, sem nenhum texto adicional:

{{
  "scores": {{ "id_do_edital_1": 8.5, "id_do_edital_2": 6.0 }}
}}"""

_STAGE2_EXPLAIN_SYSTEM = """Você é um especialista em fomento à inovação no Brasil.
Explique de forma concisa o alinhamento temático entre o perfil de uma empresa e
cada edital. Responda APENAS com JSON válido."""

_STAGE2_EXPLAIN_USER = """PERFIL DA EMPRESA:
{profile_context}

EDITAIS:
{editais_json}

Para cada edital, escreva uma justificativa curta (máx. ~200 caracteres) e duas
dimensões em 1 frase cada:

{{
  "explicacoes": [
    {{
      "id": "id_do_edital",
      "justificativa": "A empresa atua em bioeconomia, alinhada ao foco do edital em...",
      "dimensoes": {{
        "setor": "explicação em 1 frase",
        "problema_solucao": "explicação em 1 frase"
      }}
    }}
  ]
}}"""


def _make_client():
    from core.llm.llm_client import make_client
    backend = os.getenv("LLM_BACKEND", "openai").lower()

    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return make_client(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return make_client(api_key=api_key), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _editais_summary(results: list[Stage1Result]) -> list[dict]:
    """Resumo compacto dos editais (campos que o Stage 2 usa para julgar fit)."""
    out = []
    for r in results:
        c = r.card
        out.append({
            "id": c["id"],
            "title": c.get("title", ""),
            "themes": c.get("themes", []),
            "eligible_sectors": c.get("eligible_sectors", c.get("themes", [])),
            "objective": c.get("objective"),
            "key_requirements": c.get("key_requirements", [])[:3],
        })
    return out


def _temporal_block() -> str:
    """Bloco "hoje é X" para consciência temporal do Stage 2. Vazio se indisponível."""
    try:
        from core.kg.temporal import render_match_temporal_block
        return render_match_temporal_block()
    except Exception as e:
        logger.debug("match Stage 2: temporal block indisponível: %s", e)
        return ""


def _salvage_scores(raw: str) -> dict:
    """Recupera pares id→score de um JSON de scores possivelmente truncado.

    O schema 2a é um mapa plano `{"scores": {id: número}}` — então mesmo um corte
    no meio ainda deixa pares `"id": número` íntegros, que extraímos por regex.
    Salva o ranking do que o modelo já tinha decidido antes do corte.
    """
    scores = {}
    for k, v in re.findall(r'"([^"]+)"\s*:\s*([0-9]+(?:\.[0-9]+)?)', raw):
        if k != "scores":  # ignora a chave do wrapper
            scores[k] = float(v)
    return {"scores": scores}


def _stage2_chat(system: str, user: str, *, max_tokens: int, salvage=None) -> dict:
    """Chamada do Stage 2 com guardrails.

    `response_format=json_object` garante envelope JSON válido; se ainda assim o
    modelo parar por `length`, logamos ALTO (não mascaramos) e — quando há
    `salvage` — recuperamos o parcial em vez de descartar tudo em silêncio.
    """
    client, model = _make_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    choice = response.choices[0]
    raw = (choice.message.content or "").strip()
    if choice.finish_reason == "length":
        n = getattr(response.usage, "completion_tokens", "?")
        logger.error(
            "Stage 2 TRUNCADO (finish_reason=length, %s/%s tokens) — output excedeu "
            "o orçamento. %s", n, max_tokens,
            "Salvando o parcial." if salvage else "Sem salvador para este schema.",
        )
        if salvage is not None:
            return salvage(raw)
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
    return json.loads(raw)


def _call_stage2_scores(eligible: list[Stage1Result], profile: CompanyProfile) -> dict[str, float]:
    """Stage 2a — pontuação temática de TODOS os elegíveis. `{id: score}`.

    Output minúsculo e limitado (não trunca). Em falha total devolve {} (logado
    alto); o ranking degrada para Stage 1, mas sem o silêncio do `return {}` antigo.

    Backend selecionável por `MATCH_STAGE2A_BACKEND` (default `llm`). Com
    `embeddings`, troca a geração-LLM por cosseno(perfil × edital) determinístico
    (mesmo contrato `{id: float}`). NÃO promovido: default segue LLM; o caminho
    de embeddings é gated por experimento na suíte `matching` (ver BACKLOG).
    """
    if os.getenv("MATCH_STAGE2A_BACKEND", "llm").lower() == "embeddings":
        from core.match_embeddings import score_stage2a_embeddings
        return score_stage2a_embeddings(eligible, profile)
    try:
        temporal_block = _temporal_block()
        user = _STAGE2_SCORE_USER.format(
            temporal_block=f"{temporal_block}\n\n" if temporal_block else "",
            profile_context=profile.to_context(),
            editais_json=json.dumps(_editais_summary(eligible), ensure_ascii=False, indent=2),
        )
        data = _stage2_chat(_STAGE2_SCORE_SYSTEM, user, max_tokens=1500, salvage=_salvage_scores)
        return {
            k: float(v) for k, v in (data.get("scores") or {}).items()
            if isinstance(v, (int, float))
        }
    except Exception as e:
        logger.error("Stage 2a (scoring) falhou: %s", e)
        return {}


def _call_stage2_explain(
    top_results: list[Stage1Result], profile: CompanyProfile
) -> dict[str, dict]:
    """Stage 2b — justificativa + dimensões SÓ do top-K exibido.

    Output limitado por K (não pelo catálogo). Em falha devolve {} — é cosmético:
    o ranking não depende disto, só a prosa de exibição.
    """
    try:
        user = _STAGE2_EXPLAIN_USER.format(
            profile_context=profile.to_context(),
            editais_json=json.dumps(_editais_summary(top_results), ensure_ascii=False, indent=2),
        )
        data = _stage2_chat(_STAGE2_EXPLAIN_SYSTEM, user, max_tokens=1500)
        return {a["id"]: a for a in (data.get("explicacoes") or []) if "id" in a}
    except Exception as e:
        logger.warning("Stage 2b (explicação) falhou — top-K sem prosa: %s", e)
        return {}


# =============================================================================
# SERVIÇO PRINCIPAL
# =============================================================================

class HybridMatchService:
    """Matching híbrido: Stage 1 determinístico + Stage 2 semântico."""

    def __init__(self):
        self._index: dict = {}
        self._load_index()

    def _load_index(self) -> None:
        self._index = kg_store.load_index()

    def _load_wiki_page(self, edital_id: str) -> dict | None:
        # Tier 2: lê do store durável (Postgres em prod; arquivo em dev) via kg_store
        # — não mais do arquivo direto, que não existe na imagem de produção.
        return kg_store.load_wiki_page(edital_id)

    def _get_editais_with_cards(self) -> list[dict]:
        """Retorna entradas do índice enriquecidas com dados do card quando disponível.

        Defesa-em-profundidade de vigência (§7.1 WIKI.md): o índice já é
        filtrado por prazo no build, mas o cron pode não rebuildar entre dois
        prazos vencendo — então um edital ABERTA cujo prazo passou ficaria
        stale no index.json. Re-filtramos em runtime para nunca recomendar
        edital com prazo vencido. Prazo ausente (fluxo contínuo) = mantido,
        espelhando `_deadline_expired` do build (None → não expirado).
        """
        self._load_index()
        today = date.today()
        result = []
        skipped_expired = 0
        for entry in self._index.get("editais", []):
            dl = parse_deadline(entry.get("deadline"))
            if dl is not None and dl < today:
                skipped_expired += 1
                continue
            card = self._load_wiki_page(entry["id"])
            if card:
                # opportunity_type e verificacao são inherited (vêm do scrape/
                # Descoberta → entry do índice); a wiki page sintetizada NÃO os
                # carrega. Threadamos da entry via spread em dict NOVO —
                # load_wiki_page devolve objeto de blob cacheado (modo postgres),
                # mutar in-place corromperia o cache.
                result.append({
                    **card,
                    "opportunity_type": entry.get("opportunity_type", "edital"),
                    "verificacao": entry.get("verificacao", "verificado"),
                })
            else:
                result.append(entry)
        if skipped_expired:
            logger.warning(
                "%d edital(is) com prazo vencido ignorado(s) em runtime — "
                "índice stale (reference_date=%s). Rode build_knowledge_graph.",
                skipped_expired, self._index.get("reference_date", "?"),
            )
        return result

    def get_stats(self) -> dict:
        self._load_index()
        summary = self._index.get("summary", {})
        n_wiki_pages = len(iter_wiki_pages())
        return {
            "total_editais": self._index.get("total_editais", 0),
            "last_updated": self._index.get("last_updated", ""),
            "by_status": summary.get("by_status", {}),
            "n_themes": summary.get("n_themes", 0),
            "n_fontes": summary.get("n_fontes", 0),
            "n_wiki_pages": n_wiki_pages,
        }

    def list_editais(self, status: str | None = None, tema: str | None = None, limit: int = 100) -> list[dict]:
        self._load_index()
        editais = self._index.get("editais", [])
        if status:
            editais = [e for e in editais if e.get("status", "").upper() == status.upper()]
        if tema:
            tema_lower = tema.lower()
            editais = [e for e in editais if any(tema_lower in t.lower() for t in e.get("themes", []))]
        return editais[:limit]

    def get_edital_by_id(self, edital_id: str) -> dict | None:
        card = self._load_wiki_page(edital_id)
        if card:
            return card
        self._load_index()
        for e in self._index.get("editais", []):
            if e["id"] == edital_id:
                return e
        return None

    def match(
        self,
        profile: CompanyProfile,
        top_k: int = 10,
        workspace_id: str | None = None,
    ) -> list[dict]:
        """Executa matching híbrido e retorna top_k editais rankeados.

        `workspace_id` permite usar pesos customizados do workspace (com
        merge sobre os globais). Default lê apenas os globais.
        """
        weights = get_weights(workspace_id)
        editais = self._get_editais_with_cards()

        # --- Stage 1: scoring determinístico ---
        stage1_results = [score_stage1(e, profile, weights) for e in editais]
        eligible = [r for r in stage1_results if r.eligible]
        eliminated = len(stage1_results) - len(eligible)

        logger.info("Stage 1: %d elegíveis, %d eliminados", len(eligible), eliminated)

        # Sinaliza explicitamente o caso "nenhum elegível" em vez de mascarar
        # (Front 2). Antes, o fallback devolvia o top sem filtro como se fossem
        # recomendações — podia empurrar um edital inelegível pro usuário. Agora
        # ainda devolvemos os mais próximos (utilidade), mas cada item carrega
        # `eligible=False` + um aviso, para o frontend exibir o sinal de que
        # NENHUM edital passou o Stage 1.
        no_eligible = not eligible
        if no_eligible:
            logger.warning(
                "Nenhum edital passou o Stage 1 — devolvendo aproximados marcados "
                "como inelegíveis (eligible=False)",
            )
            eligible = sorted(stage1_results, key=lambda r: r.score, reverse=True)[:top_k]

        # --- Stage 2a: SCORING temático de todos os elegíveis (só {id: score}) ---
        semantic_scores: dict[str, float] = {}
        if eligible:
            semantic_scores = _call_stage2_scores(eligible, profile)

        # --- Combina scores determinístico + semântico ---
        combined = []
        for r in eligible:
            score_tematico = float(semantic_scores.get(r.edital_id, 5.0))

            # Score final: 60% determinístico (normalizado 0-10) + 40% semântico
            score_det_norm = r.score / 10.0
            score_final = round(0.6 * score_det_norm + 0.4 * score_tematico, 1)

            item = {
                "id": r.edital_id,
                "title": r.card.get("title", ""),
                "opportunity_type": r.card.get("opportunity_type", "edital"),
                # provisorio = item da Descoberta ainda sem verificação humana —
                # o frontend rotula (badge), não filtra (decisão Fase 1).
                "verificacao": r.card.get("verificacao", "verificado"),
                "status": r.card.get("status", ""),
                "deadline": r.card.get("deadline", ""),
                "score": min(score_final, 10.0),
                "score_deterministic": r.score,
                "score_tematico": score_tematico,
                "eligible": not no_eligible,
                "match_dimensions": {
                    dim: {"score": pts, "max": round(_w(weights, dim))}
                    for dim, pts in r.breakdown.items()
                },
                # Prosa preenchida no Stage 2b, só para o top-K (abaixo).
                "dimensoes_semanticas": {},
                "justificativa": "",
                "key_requirements": r.card.get("key_requirements", []),
                "objective": r.card.get("objective"),
            }
            if no_eligible:
                item["eligibility_warning"] = (
                    "Nenhum edital passou o filtro de elegibilidade (Stage 1). "
                    "Este é um dos mais próximos do seu perfil, mas pode não ser "
                    "elegível — confira os requisitos antes de aplicar."
                )
            combined.append(item)

        combined.sort(key=lambda x: x["score"], reverse=True)
        top = combined[:top_k]

        # --- Stage 2b: EXPLICAÇÃO (justificativa/dimensões) só do top-K exibido ---
        if top and not no_eligible:
            top_ids = {it["id"] for it in top}
            top_results = [r for r in eligible if r.edital_id in top_ids]
            explanations = _call_stage2_explain(top_results, profile)
            for it in top:
                ex = explanations.get(it["id"], {})
                it["justificativa"] = ex.get("justificativa", "")
                it["dimensoes_semanticas"] = ex.get("dimensoes", {})

        return top
