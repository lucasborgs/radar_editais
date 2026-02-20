"""
Perfil da Empresa (Radar de Editais)

Estrutura os dados da empresa do usuario para comparacao
com editais (Contexto A vs Contexto B).
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROFILES_DIR = Path("profiles")


@dataclass
class CompanyProfile:
    """Perfil estruturado da empresa para analise de aderencia."""

    nome: str = ""
    cnpj: str = ""
    descricao_atividades: str = ""
    cnaes: list[str] = field(default_factory=list)
    portfolio_projetos: str = ""
    tamanho_empresa: str = ""  # MEI, ME, EPP, MEDIO, GRANDE
    localizacao: str = ""
    capital_social: Optional[float] = None
    certificacoes: list[str] = field(default_factory=list)
    equipe_resumo: str = ""

    def to_context(self) -> str:
        """Gera texto de contexto para uso em prompts LLM."""
        parts = []

        if self.nome:
            parts.append(f"Empresa: {self.nome}")
        if self.cnpj:
            parts.append(f"CNPJ: {self.cnpj}")
        if self.tamanho_empresa:
            parts.append(f"Porte: {self.tamanho_empresa}")
        if self.localizacao:
            parts.append(f"Localidade: {self.localizacao}")
        if self.capital_social:
            parts.append(f"Capital Social: R$ {self.capital_social:,.2f}")
        if self.cnaes:
            parts.append(f"CNAEs: {', '.join(self.cnaes)}")
        if self.certificacoes:
            parts.append(f"Certificacoes: {', '.join(self.certificacoes)}")
        if self.descricao_atividades:
            parts.append(f"\nAtividades:\n{self.descricao_atividades}")
        if self.portfolio_projetos:
            parts.append(f"\nPortfolio de Projetos:\n{self.portfolio_projetos}")
        if self.equipe_resumo:
            parts.append(f"\nEquipe:\n{self.equipe_resumo}")

        return "\n".join(parts) if parts else "Perfil da empresa nao preenchido."

    def is_complete(self) -> bool:
        """Verifica se os campos minimos estao preenchidos."""
        return bool(self.nome and self.descricao_atividades)

    def completion_pct(self) -> int:
        """Retorna percentual de preenchimento do perfil."""
        fields_check = [
            bool(self.nome),
            bool(self.cnpj),
            bool(self.descricao_atividades),
            len(self.cnaes) > 0,
            bool(self.portfolio_projetos),
            bool(self.tamanho_empresa),
            bool(self.localizacao),
            self.capital_social is not None,
            len(self.certificacoes) > 0,
            bool(self.equipe_resumo),
        ]
        return int(sum(fields_check) / len(fields_check) * 100)

    def save(self, path: Optional[Path] = None) -> Path:
        """Salva perfil em disco como JSON."""
        if path is None:
            PROFILES_DIR.mkdir(exist_ok=True)
            slug = self.cnpj.replace("/", "").replace(".", "").replace("-", "") if self.cnpj else "default"
            path = PROFILES_DIR / f"{slug}.json"

        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Perfil salvo em {path}")
        return path

    @classmethod
    def load(cls, path: Path) -> "CompanyProfile":
        """Carrega perfil de disco."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    @classmethod
    def load_default(cls) -> "CompanyProfile":
        """Carrega perfil default se existir, senao retorna vazio."""
        default_path = PROFILES_DIR / "default.json"
        if default_path.exists():
            return cls.load(default_path)
        return cls()
