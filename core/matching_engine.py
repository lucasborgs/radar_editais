"""
Matching Engine (Radar de Editais)

Calcula score de compatibilidade empresa↔edital usando dados estruturados.
Scoring determinístico, instantâneo e explicável (sem LLM).

Fluxo: CompanyProfile + editais enriquecidos → lista rankeada com scores decompostos.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np

from domain.user_profile import CompanyProfile

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

from config import SILVER_ENRICHED_DIR as ENRICHED_PATH
ENRICHED_FILE = ENRICHED_PATH / "editais_enriched.parquet"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# PESOS DO SCORING
# =============================================================================

WEIGHTS = {
    "theme_match":              30,  # Temáticas alinhadas com atividades (+10 absorvido do cnae_match)
    "trl_match":                10,  # TRL do projeto dentro do range do mecanismo
    "porte_match":              15,  # Porte aceito
    "capital_match":             5,  # Capital social suficiente (reduzido)
    "location_match":           10,  # Localização compatível
    "certification_match":       5,  # Certificações atendidas
    "entity_type_match":        10,  # Tipo de entidade elegível
    "uso_match":                 5,  # Uso do recurso compatível com intenção do perfil
    "tipo_financiamento_match":  5,  # Tipo de instrumento alinhado com preferência
    "valor_match":               5,  # Valor buscado dentro da faixa do edital
}
# Total: 100 pontos


# =============================================================================
# FUNÇÕES DE MATCHING
# =============================================================================

def _safe_list(val) -> list:
    """Converte valor para lista segura (suporta list, ndarray, str)."""
    if val is None:
        return []
    if isinstance(val, np.ndarray):
        return val.tolist()
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [val] if val else []
    try:
        if pd.isna(val):
            return []
    except (TypeError, ValueError):
        pass
    return list(val) if hasattr(val, '__iter__') else []


def _normalize(text: str) -> str:
    """Normaliza texto para comparação."""
    return text.lower().strip().replace("-", "").replace("/", "").replace(".", "")


def score_theme_match(profile: CompanyProfile, edital_themes: list, edital_keywords: list) -> tuple[float, list[str]]:
    """Score por alinhamento temático (comparação textual simples)."""
    if not edital_themes and not edital_keywords:
        return 0.5, ["Sem temáticas definidas no edital"]

    # Termos da empresa
    company_terms = set()
    for text in [profile.descricao_atividades, profile.portfolio_projetos]:
        if text:
            company_terms.update(word.lower() for word in text.split() if len(word) > 3)

    # Termos do edital
    edital_terms = set()
    for theme in edital_themes:
        edital_terms.update(word.lower() for word in theme.split() if len(word) > 3)
    for kw in edital_keywords:
        edital_terms.add(kw.lower())

    if not edital_terms or not company_terms:
        return 0.3, ["Dados insuficientes para comparar temáticas"]

    overlap = company_terms & edital_terms
    ratio = len(overlap) / max(len(edital_terms), 1)

    # Score: 0-1
    score = min(ratio * 3, 1.0)  # 3x boost pois overlap parcial já é relevante

    reasons = []
    if overlap:
        reasons.append(f"Termos em comum: {', '.join(list(overlap)[:5])}")
    if score < 0.3:
        reasons.append("Baixa sobreposição temática")

    return score, reasons



def score_porte_match(profile: CompanyProfile, required_porte: list) -> tuple[float, list[str]]:
    """Score por porte empresarial."""
    if not required_porte:
        return 1.0, ["Edital não restringe porte"]

    if not profile.tamanho_empresa:
        return 0.5, ["Porte da empresa não informado"]

    porte_norm = _normalize(profile.tamanho_empresa)
    required_norm = {_normalize(p) for p in required_porte}

    if porte_norm in required_norm:
        return 1.0, [f"Porte '{profile.tamanho_empresa}' aceito"]

    return 0.0, [f"Porte '{profile.tamanho_empresa}' não aceito. Exigidos: {', '.join(required_porte)}"]


def score_capital_match(profile: CompanyProfile, min_capital: Optional[float]) -> tuple[float, list[str]]:
    """Score por capital social mínimo."""
    if min_capital is None:
        return 1.0, ["Edital não exige capital social mínimo"]

    if profile.capital_social is None:
        return 0.3, [f"Capital social não informado. Mínimo exigido: R$ {min_capital:,.2f}"]

    if profile.capital_social >= min_capital:
        return 1.0, [f"Capital social R$ {profile.capital_social:,.2f} >= R$ {min_capital:,.2f}"]

    ratio = profile.capital_social / min_capital
    return ratio * 0.5, [f"Capital social insuficiente: R$ {profile.capital_social:,.2f} < R$ {min_capital:,.2f}"]


def score_location_match(profile: CompanyProfile, geographic_restriction: Optional[str]) -> tuple[float, list[str]]:
    """Score por restrição geográfica."""
    if not geographic_restriction:
        return 1.0, ["Sem restrição geográfica"]

    if not profile.localizacao:
        return 0.5, [f"Localização não informada. Restrição: {geographic_restriction}"]

    loc_norm = _normalize(profile.localizacao)
    restriction_norm = _normalize(geographic_restriction)

    # Verificação simples de containment
    if loc_norm in restriction_norm or restriction_norm in loc_norm:
        return 1.0, [f"Localização '{profile.localizacao}' compatível"]

    # Verifica estado/cidade parcial
    loc_parts = {p.strip() for p in profile.localizacao.lower().replace("/", " ").split()}
    restriction_parts = {p.strip() for p in geographic_restriction.lower().replace("/", " ").split()}
    if loc_parts & restriction_parts:
        return 0.8, [f"Localização parcialmente compatível"]

    return 0.2, [f"Possível restrição geográfica: {geographic_restriction}"]


def score_certification_match(profile: CompanyProfile, required_certs: list) -> tuple[float, list[str]]:
    """Score por certificações exigidas."""
    if not required_certs:
        return 1.0, ["Edital não exige certificações"]

    if not profile.certificacoes:
        return 0.2, [f"Certificações exigidas não atendidas: {', '.join(required_certs)}"]

    profile_certs = {_normalize(c) for c in profile.certificacoes}
    required_norm = {_normalize(c) for c in required_certs}

    matched = sum(1 for r in required_norm if any(r in pc or pc in r for pc in profile_certs))
    ratio = matched / len(required_norm)

    if ratio >= 1.0:
        return 1.0, ["Todas as certificações atendidas"]
    elif ratio > 0:
        return ratio, [f"{matched}/{len(required_certs)} certificações atendidas"]
    return 0.0, [f"Nenhuma certificação atendida. Exigidas: {', '.join(required_certs)}"]


def score_trl_match(
    profile_trl: Optional[int],
    trl_min: Optional[int],
    trl_max: Optional[int],
) -> tuple[float, list[str]]:
    """Score por TRL (Technology Readiness Level) do projeto da empresa."""
    if trl_min is None and trl_max is None:
        return 1.0, ["Maturidade tecnológica não especificada pelo mecanismo"]

    if profile_trl is None:
        return 0.5, ["TRL da empresa não informado — preencha o campo 'trl' no perfil"]

    too_low = trl_min is not None and profile_trl < trl_min
    too_high = trl_max is not None and profile_trl > trl_max

    if not too_low and not too_high:
        trl_range = f"TRL {trl_min}-{trl_max}" if trl_min and trl_max else f"TRL {trl_min or trl_max}"
        return 1.0, [f"TRL {profile_trl} dentro do range do mecanismo ({trl_range})"]

    if too_low:
        gap = trl_min - profile_trl
        penalty = min(gap * 0.25, 1.0)
        return max(0.0, 1.0 - penalty), [
            f"TRL do projeto ({profile_trl}) abaixo do mínimo exigido ({trl_min}) — projeto ainda imaturo para este mecanismo"
        ]

    # too_high
    return 0.6, [
        f"TRL do projeto ({profile_trl}) acima do máximo do mecanismo ({trl_max}) — projeto pode estar avançado demais para esta fase"
    ]


TIPO_INSTRUMENTO_TO_FINANCIAMENTO = {
    "SUBVENCAO_ECONOMICA":   "subvencao_nao_reembolsavel",
    "PESQUISA_BASICA":       "pesquisa_colaborativa",
    "PESQUISA_APLICADA":     "pesquisa_colaborativa",
    "CREDITO_INOVACAO":      "credito_reembolsavel",
    "ENCOMENDA_TECNOLOGICA": "subvencao_nao_reembolsavel",
    "MATCHING_EMBRAPII":     "matching_embrapii",
}


def score_tipo_financiamento_match(
    profile_tipos: list,
    tipo_instrumento: Optional[str],
) -> tuple[float, list[str]]:
    """Score por alinhamento entre preferência de financiamento e instrumento do edital."""
    if not profile_tipos or not tipo_instrumento:
        return 0.5, ["Preferência de financiamento ou tipo de instrumento não especificados"]
    mapped = TIPO_INSTRUMENTO_TO_FINANCIAMENTO.get(tipo_instrumento, "")
    if mapped and mapped in profile_tipos:
        return 1.0, [f"Tipo de financiamento '{mapped}' alinhado com preferência do perfil"]
    tipo_label = tipo_instrumento if not mapped else f"{tipo_instrumento} ({mapped})"
    return 0.0, [f"Instrumento '{tipo_label}' não está entre os tipos de interesse do perfil"]


def score_uso_match(
    profile_usos: list,
    allowed_uses: list,
) -> tuple[float, list[str]]:
    """Score por compatibilidade entre uso pretendido e usos permitidos pelo edital."""
    if not profile_usos or not allowed_uses:
        return 0.5, ["Uso do recurso não especificado no perfil ou no edital"]
    overlap = set(profile_usos) & set(allowed_uses)
    if overlap:
        return 1.0, [f"Uso compatível: {', '.join(sorted(overlap))}"]
    return 0.0, [f"Uso desejado ({', '.join(profile_usos)}) não está entre os usos permitidos pelo edital"]


def score_valor_match(
    valor_buscado: Optional[float],
    value_min_brl: Optional[float],
    value_max_brl: Optional[float],
) -> tuple[float, list[str]]:
    """Score por adequação do valor buscado à faixa do edital."""
    if valor_buscado is None or (value_min_brl is None and value_max_brl is None):
        return 0.5, ["Valor buscado ou faixa do edital não especificados"]
    if value_min_brl is not None and valor_buscado < value_min_brl:
        return 0.0, [f"Valor buscado R${valor_buscado:,.0f} abaixo do mínimo R${value_min_brl:,.0f}"]
    if value_max_brl is not None and valor_buscado > value_max_brl:
        return 0.2, [f"Valor buscado R${valor_buscado:,.0f} acima do máximo R${value_max_brl:,.0f}"]
    return 1.0, [f"Valor R${valor_buscado:,.0f} dentro da faixa do edital"]


def score_entity_type_match(profile: CompanyProfile, eligible_types: list) -> tuple[float, list[str]]:
    """Score por tipo de entidade elegível."""
    if not eligible_types:
        return 1.0, ["Sem restrição de tipo de entidade"]

    # Inferir tipo da empresa pelo perfil
    types_norm = {_normalize(t) for t in eligible_types}

    # Heurísticas simples
    empresa_terms = {"empresa", "empresas", "pj", "pessoa juridica", "micro empresa", "startup"}
    if types_norm & empresa_terms:
        return 1.0, ["Empresas são elegíveis"]

    # Se aceita ICT/universidade e empresa não é uma, penaliza
    academic_terms = {"ict", "universidade", "instituicao de pesquisa", "pesquisador"}
    if types_norm & academic_terms and not (types_norm & empresa_terms):
        return 0.1, [f"Edital parece restrito a: {', '.join(eligible_types)}"]

    return 0.5, [f"Verificar elegibilidade: {', '.join(eligible_types)}"]


# =============================================================================
# ENGINE PRINCIPAL
# =============================================================================

class MatchingEngine:
    """
    Calcula compatibilidade empresa↔editais usando dados estruturados.
    Score instantâneo, determinístico e explicável.
    """

    def __init__(self):
        self.df = None
        self._load_data()

    def _load_data(self):
        """Carrega dados enriquecidos."""
        if not ENRICHED_FILE.exists():
            logger.warning(f"Dados enriquecidos não encontrados: {ENRICHED_FILE}")
            logger.warning("Execute primeiro: python etl_enrichment.py")
            self.df = pd.DataFrame()
            return

        self.df = pd.read_parquet(ENRICHED_FILE)
        logger.info(f"MatchingEngine: {len(self.df)} editais carregados")

    def reload(self):
        """Recarrega dados do disco."""
        self._load_data()

    def match(
        self,
        profile: CompanyProfile,
        top_k: int = 20,
        min_score: float = 0,
        source_filter: Optional[list[str]] = None,
        status_filter: Optional[str] = "ABERTA",
        tipo_fluxo_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Calcula match empresa↔editais/unidades.

        Args:
            tipo_fluxo_filter: "EDITAL", "FLUXO_CONTINUO" ou None (ambos).
              Mecanismos de fluxo contínuo (Embrapii) são sempre incluídos a menos
              que tipo_fluxo_filter=="EDITAL".

        Returns:
            Lista de dicts com score, breakdown, metadados do edital.
            Ordenada por score desc.
        """
        if self.df is None or self.df.empty:
            return []

        df = self.df.copy()

        # Filtros
        if tipo_fluxo_filter:
            if "tipo_fluxo" in df.columns:
                df = df[df["tipo_fluxo"] == tipo_fluxo_filter]
        elif status_filter:
            # Para editais: filtra por status; fluxo contínuo não tem status "ABERTA"
            if "tipo_fluxo" in df.columns:
                editais_mask = (df["tipo_fluxo"] == "EDITAL") & (df["status"] == status_filter)
                continuo_mask = df["tipo_fluxo"] == "FLUXO_CONTINUO"
                df = df[editais_mask | continuo_mask]
            else:
                df = df[df["status"] == status_filter]
        if source_filter:
            df = df[df["source"].isin(source_filter)]

        results = []

        for _, row in df.iterrows():
            score_result = self._score_edital(profile, row)
            if score_result["total_score"] >= min_score:
                results.append(score_result)

        # Ordena por score desc
        results.sort(key=lambda x: x["total_score"], reverse=True)

        return results[:top_k]

    def _score_edital(self, profile: CompanyProfile, row: pd.Series) -> dict:
        """Calcula score detalhado para um edital/unidade."""
        breakdown = {}
        reasons_all = []
        tipo_fluxo = str(row.get("tipo_fluxo", "") or "")
        tipo_instrumento = str(row.get("tipo_instrumento", "") or "")

        # 1. Theme match
        themes = _safe_list(row.get("themes"))
        keywords = _safe_list(row.get("keywords"))
        score, reasons = score_theme_match(profile, themes, keywords)
        breakdown["theme_match"] = round(score * WEIGHTS["theme_match"], 1)
        reasons_all.extend(reasons)

        # 2. TRL match
        trl_min_raw = row.get("trl_min")
        trl_max_raw = row.get("trl_max")
        trl_min = int(trl_min_raw) if trl_min_raw is not None and pd.notna(trl_min_raw) else None
        trl_max = int(trl_max_raw) if trl_max_raw is not None and pd.notna(trl_max_raw) else None
        score, reasons = score_trl_match(profile.trl, trl_min, trl_max)
        breakdown["trl_match"] = round(score * WEIGHTS["trl_match"], 1)
        reasons_all.extend(reasons)

        # 4. Porte match
        porte = _safe_list(row.get("required_porte"))
        score, reasons = score_porte_match(profile, porte)
        breakdown["porte_match"] = round(score * WEIGHTS["porte_match"], 1)
        reasons_all.extend(reasons)

        # 5. Capital social
        min_capital = row.get("min_capital_social")
        if pd.isna(min_capital) if isinstance(min_capital, float) else min_capital is None:
            min_capital = None
        score, reasons = score_capital_match(profile, min_capital)
        breakdown["capital_match"] = round(score * WEIGHTS["capital_match"], 1)
        reasons_all.extend(reasons)

        # 6. Location match
        geo = row.get("geographic_restriction")
        if pd.isna(geo) if isinstance(geo, float) else geo is None:
            geo = None
        score, reasons = score_location_match(profile, geo)
        breakdown["location_match"] = round(score * WEIGHTS["location_match"], 1)
        reasons_all.extend(reasons)

        # 7. Certification match
        certs = _safe_list(row.get("required_certifications"))
        score, reasons = score_certification_match(profile, certs)
        breakdown["certification_match"] = round(score * WEIGHTS["certification_match"], 1)
        reasons_all.extend(reasons)

        # 8. Entity type match
        entity_types = _safe_list(row.get("eligible_entity_types"))
        score, reasons = score_entity_type_match(profile, entity_types)
        breakdown["entity_type_match"] = round(score * WEIGHTS["entity_type_match"], 1)
        reasons_all.extend(reasons)

        # 9. Tipo de financiamento match
        score, reasons = score_tipo_financiamento_match(
            profile.tipos_financiamento_interesse, tipo_instrumento or None
        )
        breakdown["tipo_financiamento_match"] = round(score * WEIGHTS["tipo_financiamento_match"], 1)
        reasons_all.extend(reasons)

        # 10. Uso do recurso match
        allowed_uses = _safe_list(row.get("allowed_use_of_funds"))
        score, reasons = score_uso_match(profile.uso_financiamento, allowed_uses)
        breakdown["uso_match"] = round(score * WEIGHTS["uso_match"], 1)
        reasons_all.extend(reasons)

        # 11. Valor match
        value_min = row.get("value_min_brl")
        value_max = row.get("value_max_brl")
        if isinstance(value_min, float) and pd.isna(value_min):
            value_min = None
        if isinstance(value_max, float) and pd.isna(value_max):
            value_max = None
        score, reasons = score_valor_match(profile.valor_buscado, value_min, value_max)
        breakdown["valor_match"] = round(score * WEIGHTS["valor_match"], 1)
        reasons_all.extend(reasons)

        total_score = sum(breakdown.values())

        # Classificação
        if total_score >= 75:
            recommendation = "ALTA_ADERENCIA"
        elif total_score >= 50:
            recommendation = "MEDIA_ADERENCIA"
        elif total_score >= 30:
            recommendation = "BAIXA_ADERENCIA"
        else:
            recommendation = "INCOMPATIVEL"

        # Deadline: suprimido para mecanismos de fluxo contínuo
        deadline_str = None
        if tipo_fluxo != "FLUXO_CONTINUO":
            deadline = row.get("deadline_date")
            if deadline is not None and not (isinstance(deadline, float) and pd.isna(deadline)):
                if hasattr(deadline, "isoformat"):
                    deadline_str = deadline.isoformat()
                else:
                    deadline_str = str(deadline)

        # Nota de fluxo contínuo
        if tipo_fluxo == "FLUXO_CONTINUO":
            reasons_all.append(
                "Mecanismo de fluxo contínuo (sempre aberto) — não há prazo de submissão. "
                "Exige contrapartida financeira e participação em projeto conjunto com a unidade Embrapii."
            )

        return {
            "edital_id": row["id"],
            "title": str(row.get("title", "")),
            "source": str(row.get("source", "")),
            "url": str(row.get("url", "")),
            "status": str(row.get("status", "")),
            "tipo_fluxo": tipo_fluxo,
            "tipo_instrumento": tipo_instrumento,
            "trl_min": trl_min,
            "trl_max": trl_max,
            "deadline_date": deadline_str,
            "category": str(row.get("category", "")),
            "themes": themes,
            "total_score": round(total_score, 1),
            "breakdown": breakdown,
            "reasons": reasons_all,
            "recommendation": recommendation,
            "description_preview": str(row.get("description", ""))[:300],
        }

    def get_edital_by_id(self, edital_id: str) -> Optional[dict]:
        """Retorna dados completos de um edital pelo ID."""
        if self.df is None or self.df.empty:
            return None
        match = self.df[self.df["id"] == edital_id]
        if match.empty:
            return None
        row = match.iloc[0]
        return row.to_dict()

    def list_editais(
        self,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """Retorna lista de editais com filtros opcionais. Usado pela API REST."""
        if self.df is None or self.df.empty:
            return []
        df = self.df.copy()
        if status:
            df = df[df["status"] == status]
        if source:
            df = df[df["source"] == source]
        df = df.head(limit)

        rows = []
        for _, row in df.iterrows():
            # Normaliza deadline
            deadline = row.get("deadline_date")
            deadline_str = None
            if deadline is not None and not (isinstance(deadline, float) and pd.isna(deadline)):
                deadline_str = deadline.isoformat() if hasattr(deadline, "isoformat") else str(deadline)

            # Normaliza created_at
            created = row.get("created_at")
            created_str = None
            if created is not None and not (isinstance(created, float) and pd.isna(created)):
                created_str = created.isoformat() if hasattr(created, "isoformat") else str(created)

            # Normaliza campos string opcionais
            def _str_or_none(v):
                if v is None:
                    return None
                try:
                    if pd.isna(v):
                        return None
                except (TypeError, ValueError):
                    pass
                return str(v)

            # Normaliza campos float opcionais
            def _float_or_none(v):
                if v is None:
                    return None
                try:
                    if pd.isna(v):
                        return None
                except (TypeError, ValueError):
                    pass
                return float(v)

            trl_min_raw = row.get("trl_min")
            trl_max_raw = row.get("trl_max")

            rows.append({
                "id": str(row["id"]),
                "title": str(row.get("title", "")),
                "description": str(row.get("description", "")),
                "url": str(row.get("url", "")),
                "deadline_date": deadline_str,
                "created_at": created_str,
                "status": str(row.get("status", "")),
                "tipo_fluxo": str(row.get("tipo_fluxo", "") or ""),
                "tipo_instrumento": str(row.get("tipo_instrumento", "") or ""),
                "trl_min": int(trl_min_raw) if trl_min_raw is not None and pd.notna(trl_min_raw) else None,
                "trl_max": int(trl_max_raw) if trl_max_raw is not None and pd.notna(trl_max_raw) else None,
                "extracted_at": str(row.get("extracted_at", "")),
                "category": str(row.get("category", "")),
                "target_audience": _str_or_none(row.get("target_audience")),
                "location": _str_or_none(row.get("location")),
                "value_brl": _float_or_none(row.get("value_brl")),
                "source": str(row.get("source", "")),
                "themes": _safe_list(row.get("themes")),
                "keywords": _safe_list(row.get("keywords")),
                "required_cnaes": _safe_list(row.get("required_cnaes")),
                "required_porte": _safe_list(row.get("required_porte")),
                "min_capital_social": _float_or_none(row.get("min_capital_social")),
                "required_certifications": _safe_list(row.get("required_certifications")),
                "required_docs": _safe_list(row.get("required_docs")),
                "geographic_restriction": _str_or_none(row.get("geographic_restriction")),
                "eligible_entity_types": _safe_list(row.get("eligible_entity_types")),
                "evaluation_criteria": _safe_list(row.get("evaluation_criteria")),
            })
        return rows

    def get_sources(self) -> list[str]:
        """Retorna fontes disponíveis."""
        if self.df is None or self.df.empty:
            return []
        return sorted(self.df["source"].unique().tolist())

    def get_themes(self) -> list[str]:
        """Retorna todas as temáticas únicas."""
        if self.df is None or self.df.empty:
            return []
        all_themes = set()
        if "themes" in self.df.columns:
            for themes in self.df["themes"]:
                all_themes.update(_safe_list(themes))
        return sorted(all_themes)

    def get_stats(self) -> dict:
        """Retorna estatísticas gerais."""
        if self.df is None or self.df.empty:
            return {"total": 0}
        return {
            "total": len(self.df),
            "by_source": self.df["source"].value_counts().to_dict(),
            "by_status": self.df["status"].value_counts().to_dict(),
            "themes": len(self.get_themes()),
        }


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    profile = CompanyProfile(
        nome="TechSol Inovações",
        cnpj="12.345.678/0001-90",
        descricao_atividades="Desenvolvimento de software para gestão pública, sistemas web e apps mobile. Inteligência artificial aplicada à educação.",
        portfolio_projetos="Sistema de gestão escolar para prefeitura de Campinas. App de fiscalização ambiental para IBAMA.",
        tamanho_empresa="EPP",
        localizacao="São Paulo/SP",
        capital_social=250000.0,
        certificacoes=["ISO 9001"],
        equipe_resumo="15 colaboradores. 2 engenheiros de software senior, 1 gerente de projetos PMP.",
    )

    engine = MatchingEngine()
    results = engine.match(profile, top_k=10, status_filter=None)

    print(f"\n{'='*60}")
    print(f"TOP {len(results)} EDITAIS PARA: {profile.nome}")
    print(f"{'='*60}\n")

    for i, r in enumerate(results, 1):
        print(f"{i}. [{r['total_score']:.0f}pts] {r['title'][:70]}")
        print(f"   Fonte: {r['source']} | {r['recommendation']}")
        for dim, val in r['breakdown'].items():
            bar = "█" * int(val / max(WEIGHTS.values()) * 10)
            print(f"   {dim:25s}: {val:5.1f} {bar}")
        print()
