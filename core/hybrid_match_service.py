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
from dataclasses import dataclass
from pathlib import Path

from config import KNOWLEDGE_GRAPH_DIR, KG_WIKI_DIR
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

# =============================================================================
# MAPEAMENTOS PARA STAGE 1
# =============================================================================

import unicodedata

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

# Pesos das dimensões (total = 100)
_WEIGHTS = {
    "elegibilidade":    30,
    "tematico":         25,
    "trl":              20,
    "mecanismo":        15,
    "contrapartida":    10,
}

# Editais abaixo desse score no Stage 1 são eliminados
_ELIMINATION_THRESHOLD = 25


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


def _score_elegibilidade(card: dict, profile: CompanyProfile) -> int:
    """Verifica se o tipo de entidade e porte da empresa se encaixam no edital."""
    eligible = {
        _normalize(e)
        for e in card.get("eligible_entities", []) + card.get("publico_alvo", [])
    }
    if not eligible:
        return _WEIGHTS["elegibilidade"] // 2  # sem info → neutro

    entity_labels = _ENTITY_MAP.get(_normalize(profile.tipo_entidade), set())
    porte_labels = _PORTE_MAP.get(profile.tamanho_empresa or "", set())
    all_labels = entity_labels | porte_labels

    if all_labels & eligible:
        return _WEIGHTS["elegibilidade"]

    # Fallback: se "empresas" está no edital e temos qualquer empresa
    if profile.tipo_entidade in ("empresa", "startup") and "empresas" in eligible:
        return _WEIGHTS["elegibilidade"]

    return 0


def _score_tematico(card: dict, profile: CompanyProfile) -> int:
    """Interseção de keywords do perfil com themes/eligible_sectors do edital."""
    edital_themes = (card.get("themes") or []) + (card.get("eligible_sectors") or [])
    if not edital_themes:
        return _WEIGHTS["tematico"] // 2  # sem info → neutro

    edital_kw = set()
    for theme in edital_themes:
        edital_kw |= _keywords(theme)

    profile_text = " ".join(filter(None, [
        profile.one_liner,
        profile.solution_summary,
        profile.descricao_atividades[:600] if profile.descricao_atividades else "",
    ]))
    if not profile_text.strip():
        return _WEIGHTS["tematico"] // 2

    profile_kw = _keywords(profile_text)

    if not edital_kw:
        return _WEIGHTS["tematico"] // 2

    overlap = len(edital_kw & profile_kw)
    # Normaliza pelo número de keywords do edital (máx 5 para evitar inflação)
    coverage = min(overlap / min(len(edital_kw), 5), 1.0)
    return round(_WEIGHTS["tematico"] * coverage)


def _score_trl(card: dict, profile: CompanyProfile) -> int:
    trl_range = card.get("trl_range") or {}
    trl_min = trl_range.get("min")
    trl_max = trl_range.get("max")

    if trl_min is None and trl_max is None:
        return _WEIGHTS["trl"] // 2  # sem info → neutro

    if profile.trl is None:
        return _WEIGHTS["trl"] // 2

    trl_min = trl_min or 1
    trl_max = trl_max or 9

    if trl_min <= profile.trl <= trl_max:
        return _WEIGHTS["trl"]

    # Parcial: 1 nível de distância
    if abs(profile.trl - trl_min) == 1 or abs(profile.trl - trl_max) == 1:
        return _WEIGHTS["trl"] // 2

    return 0


def _score_mecanismo(card: dict, profile: CompanyProfile) -> int:
    card_mechanism = _normalize(card.get("mechanism") or "")
    if not card_mechanism:
        return _WEIGHTS["mecanismo"] // 2  # sem info → neutro

    if not profile.tipos_financiamento_interesse:
        return _WEIGHTS["mecanismo"] // 2

    for interesse in profile.tipos_financiamento_interesse:
        accepted = _MECHANISM_MAP.get(_normalize(interesse), set())
        if card_mechanism in accepted:
            return _WEIGHTS["mecanismo"]

    return 0


def _score_contrapartida(card: dict, profile: CompanyProfile) -> int:
    """Avalia se a empresa tem capacidade de arcar com contrapartida quando exigida."""
    counterpart_required = card.get("counterpart_required")

    # Edital não exige contrapartida → ponto cheio
    if not counterpart_required:
        return _WEIGHTS["contrapartida"]

    # Exige contrapartida: verifica porte/capital
    porte = profile.tamanho_empresa or ""
    if porte in _PORTE_CONTRAPARTIDA_OK:
        return _WEIGHTS["contrapartida"]

    if porte in _PORTE_CONTRAPARTIDA_PARCIAL:
        return _WEIGHTS["contrapartida"] // 2

    # MEI/ME: capital social pode salvar se suficientemente alto
    if profile.capital_social and profile.capital_social >= 500_000:
        return _WEIGHTS["contrapartida"] // 2

    if porte in ("MEI", "ME"):
        return 0

    # Sem info de porte → neutro
    return _WEIGHTS["contrapartida"] // 2


def score_stage1(edital: dict, profile: CompanyProfile) -> Stage1Result:
    """Pontua um edital contra o perfil de empresa (Stage 1 determinístico)."""
    breakdown = {
        "elegibilidade": _score_elegibilidade(edital, profile),
        "tematico":      _score_tematico(edital, profile),
        "trl":           _score_trl(edital, profile),
        "mecanismo":     _score_mecanismo(edital, profile),
        "contrapartida": _score_contrapartida(edital, profile),
    }
    total = sum(breakdown.values())

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

_STAGE2_SYSTEM = """Você é um especialista em fomento à inovação no Brasil.

Avalie o alinhamento temático entre o perfil de uma empresa e os editais FINEP listados.
Esses editais já passaram por um filtro estrutural — foque apenas na adequação temática e setorial.

Responda APENAS com JSON válido."""

_STAGE2_USER = """PERFIL DA EMPRESA:
{profile_context}

EDITAIS ELEGÍVEIS PARA AVALIAÇÃO TEMÁTICA:
{editais_json}

Para cada edital, retorne uma pontuação temática de 0.0 a 10.0 e uma justificativa curta.
Considere: área de atuação da empresa vs temas/setores do edital, experiência prévia relevante,
e aderência do problema/solução ao foco do programa.

{{
  "avaliacoes": [
    {{
      "id": "id_do_edital",
      "score_tematico": 8.5,
      "justificativa": "A empresa atua em bioeconomia, alinhada ao foco do edital em...",
      "dimensoes": {{
        "setor": "explicação em 1 frase",
        "problema_solucao": "explicação em 1 frase"
      }}
    }}
  ]
}}"""


def _make_client():
    from openai import OpenAI
    backend = os.getenv("LLM_BACKEND", "openai").lower()

    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return OpenAI(api_key=api_key), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _call_stage2(eligible: list[Stage1Result], profile: CompanyProfile) -> dict[str, dict]:
    """Chama LLM para avaliação temática dos editais elegíveis.

    Returns:
        Dict {edital_id: {score_tematico, justificativa, dimensoes}}
    """
    client, model = _make_client()

    editais_summary = []
    for r in eligible:
        c = r.card
        editais_summary.append({
            "id": c["id"],
            "title": c.get("title", ""),
            "themes": c.get("themes", []),
            "eligible_sectors": c.get("eligible_sectors", c.get("themes", [])),
            "objective": c.get("objective"),
            "key_requirements": c.get("key_requirements", [])[:3],
        })

    prompt = _STAGE2_USER.format(
        profile_context=profile.to_context(),
        editais_json=json.dumps(editais_summary, ensure_ascii=False, indent=2),
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _STAGE2_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()

        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()

        data = json.loads(raw)
        return {a["id"]: a for a in data.get("avaliacoes", [])}

    except Exception as e:
        logger.error("Erro Stage 2 LLM: %s", e)
        return {}


# =============================================================================
# SERVIÇO PRINCIPAL
# =============================================================================

class HybridMatchService:
    """Matching híbrido: Stage 1 determinístico + Stage 2 semântico."""

    INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"

    def __init__(self):
        self._index: dict = {}
        self._index_mtime: float = 0.0
        self._load_index()

    def _load_index(self) -> None:
        if not self.INDEX_FILE.exists():
            self._index = {"editais": []}
            return
        mtime = self.INDEX_FILE.stat().st_mtime
        if mtime != self._index_mtime:
            self._index = json.loads(self.INDEX_FILE.read_text(encoding="utf-8"))
            self._index_mtime = mtime

    def _load_wiki_page(self, edital_id: str) -> dict | None:
        wiki_file = KG_WIKI_DIR / f"{edital_id}.json"
        if wiki_file.exists():
            try:
                return json.loads(wiki_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def _get_editais_with_cards(self) -> list[dict]:
        """Retorna entradas do índice enriquecidas com dados do card quando disponível."""
        self._load_index()
        result = []
        for entry in self._index.get("editais", []):
            card = self._load_wiki_page(entry["id"])
            result.append(card if card else entry)
        return result

    def get_stats(self) -> dict:
        self._load_index()
        summary = self._index.get("summary", {})
        n_wiki_pages = len(list(KG_WIKI_DIR.glob("*.json"))) if KG_WIKI_DIR.exists() else 0
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

    def match(self, profile: CompanyProfile, top_k: int = 10) -> list[dict]:
        """Executa matching híbrido e retorna top_k editais rankeados."""
        editais = self._get_editais_with_cards()
        has_cards = any(KG_WIKI_DIR / f"{e['id']}.json" for e in editais
                        if (KG_WIKI_DIR / f"{e['id']}.json").exists())

        # --- Stage 1: scoring determinístico ---
        stage1_results = [score_stage1(e, profile) for e in editais]
        eligible = [r for r in stage1_results if r.eligible]
        eliminated = len(stage1_results) - len(eligible)

        logger.info("Stage 1: %d elegíveis, %d eliminados", len(eligible), eliminated)

        if not eligible:
            logger.warning("Nenhum edital elegível após Stage 1 — devolvendo top sem filtro")
            eligible = sorted(stage1_results, key=lambda r: r.score, reverse=True)[:top_k]

        # --- Stage 2: alinhamento temático via LLM ---
        semantic_scores: dict[str, dict] = {}
        if eligible:
            semantic_scores = _call_stage2(eligible, profile)

        # --- Combina scores e monta resultado final ---
        combined = []
        for r in eligible:
            sem = semantic_scores.get(r.edital_id, {})
            score_tematico = float(sem.get("score_tematico", 5.0))

            # Score final: 60% determinístico (normalizado 0-10) + 40% semântico
            score_det_norm = r.score / 10.0
            score_final = round(0.6 * score_det_norm + 0.4 * score_tematico, 1)

            combined.append({
                "id": r.edital_id,
                "title": r.card.get("title", ""),
                "status": r.card.get("status", ""),
                "deadline": r.card.get("deadline", ""),
                "score": min(score_final, 10.0),
                "score_deterministic": r.score,
                "score_tematico": score_tematico,
                "match_dimensions": {
                    dim: {"score": pts, "max": _WEIGHTS[dim]}
                    for dim, pts in r.breakdown.items()
                },
                "dimensoes_semanticas": sem.get("dimensoes", {}),
                "justificativa": sem.get("justificativa", ""),
                "key_requirements": r.card.get("key_requirements", []),
                "objective": r.card.get("objective"),
            })

        combined.sort(key=lambda x: x["score"], reverse=True)
        return combined[:top_k]
