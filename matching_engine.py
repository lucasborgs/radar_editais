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

from user_profile import CompanyProfile

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

ENRICHED_PATH = Path("silver_data_enriched")
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
    "theme_match": 25,        # Temáticas alinhadas com atividades
    "cnae_match": 20,         # CNAE compatível
    "porte_match": 15,        # Porte aceito
    "capital_match": 10,      # Capital social suficiente
    "location_match": 10,     # Localização compatível
    "certification_match": 10, # Certificações atendidas
    "entity_type_match": 10,  # Tipo de entidade elegível
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


def score_cnae_match(profile: CompanyProfile, required_cnaes: list) -> tuple[float, list[str]]:
    """Score por compatibilidade de CNAE."""
    if not required_cnaes:
        return 1.0, ["Edital não exige CNAE específico"]

    if not profile.cnaes:
        return 0.3, ["Empresa sem CNAEs cadastrados - não é possível verificar"]

    # Normaliza para comparação (remove pontuação)
    profile_cnaes = {_normalize(c) for c in profile.cnaes}
    required_norm = {_normalize(c) for c in required_cnaes}

    # Match exato
    exact_match = profile_cnaes & required_norm
    if exact_match:
        return 1.0, [f"CNAEs compatíveis: {', '.join(exact_match)}"]

    # Match por prefixo (grupo CNAE, primeiros 4 dígitos)
    profile_prefixes = {c[:4] for c in profile_cnaes if len(c) >= 4}
    required_prefixes = {c[:4] for c in required_norm if len(c) >= 4}
    prefix_match = profile_prefixes & required_prefixes
    if prefix_match:
        return 0.7, [f"CNAEs parcialmente compatíveis (mesmo grupo): {', '.join(prefix_match)}"]

    return 0.0, [f"CNAEs incompatíveis. Exigidos: {', '.join(required_cnaes)}"]


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
    ) -> list[dict]:
        """
        Calcula match empresa↔editais.

        Returns:
            Lista de dicts com score, breakdown, metadados do edital.
            Ordenada por score desc.
        """
        if self.df is None or self.df.empty:
            return []

        df = self.df.copy()

        # Filtros
        if status_filter:
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
        """Calcula score detalhado para um edital."""
        breakdown = {}
        reasons_all = []

        # 1. Theme match
        themes = _safe_list(row.get("themes"))
        keywords = _safe_list(row.get("keywords"))
        score, reasons = score_theme_match(profile, themes, keywords)
        breakdown["theme_match"] = round(score * WEIGHTS["theme_match"], 1)
        reasons_all.extend(reasons)

        # 2. CNAE match
        cnaes = _safe_list(row.get("required_cnaes"))
        score, reasons = score_cnae_match(profile, cnaes)
        breakdown["cnae_match"] = round(score * WEIGHTS["cnae_match"], 1)
        reasons_all.extend(reasons)

        # 3. Porte match
        porte = _safe_list(row.get("required_porte"))
        score, reasons = score_porte_match(profile, porte)
        breakdown["porte_match"] = round(score * WEIGHTS["porte_match"], 1)
        reasons_all.extend(reasons)

        # 4. Capital social
        min_capital = row.get("min_capital_social")
        if pd.isna(min_capital) if isinstance(min_capital, float) else min_capital is None:
            min_capital = None
        score, reasons = score_capital_match(profile, min_capital)
        breakdown["capital_match"] = round(score * WEIGHTS["capital_match"], 1)
        reasons_all.extend(reasons)

        # 5. Location match
        geo = row.get("geographic_restriction")
        if pd.isna(geo) if isinstance(geo, float) else geo is None:
            geo = None
        score, reasons = score_location_match(profile, geo)
        breakdown["location_match"] = round(score * WEIGHTS["location_match"], 1)
        reasons_all.extend(reasons)

        # 6. Certification match
        certs = _safe_list(row.get("required_certifications"))
        score, reasons = score_certification_match(profile, certs)
        breakdown["certification_match"] = round(score * WEIGHTS["certification_match"], 1)
        reasons_all.extend(reasons)

        # 7. Entity type match
        entity_types = _safe_list(row.get("eligible_entity_types"))
        score, reasons = score_entity_type_match(profile, entity_types)
        breakdown["entity_type_match"] = round(score * WEIGHTS["entity_type_match"], 1)
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

        # Deadline
        deadline = row.get("deadline_date")
        deadline_str = None
        if deadline is not None and not (isinstance(deadline, float) and pd.isna(deadline)):
            if hasattr(deadline, "isoformat"):
                deadline_str = deadline.isoformat()
            else:
                deadline_str = str(deadline)

        return {
            "edital_id": row["id"],
            "title": str(row.get("title", "")),
            "source": str(row.get("source", "")),
            "url": str(row.get("url", "")),
            "status": str(row.get("status", "")),
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
        cnaes=["6201-5/01", "6202-3/00"],
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
