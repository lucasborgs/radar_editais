"""
Skills loader — carrega regras de compliance específicas por fonte de edital.

Fase 4 #26: cada fonte (FINEP, FAPESP) tem regras de aderência
particulares — formato de orçamento, contrapartida, prestação de contas,
linguagem esperada. Em vez de hard-coded no código, vivem em arquivos
markdown que o ComplianceMonitor e a WritingSession carregam condicionalmente
com base no campo `source` da wiki page do edital.

Adicionar suporte a uma nova fonte = adicionar `skills/<fonte>_compliance.md`
e (opcionalmente) outros tipos de skills (`<fonte>_writing.md`, etc.).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# Tipos de skills suportados. Cada um vira `skills/<source>_<type>.md`.
SKILL_COMPLIANCE = "compliance"
SKILL_WRITING = "writing"


def _normalize_source(source: str) -> str:
    """Normaliza source para lookup: lowercase + remove sufixos comuns."""
    return source.lower().strip().replace(" ", "_")


def load_skill(source: str, skill_type: str = SKILL_COMPLIANCE) -> str:
    """Lê o arquivo skill correspondente.

    Returns: conteúdo do markdown como string, ou "" se não houver skill para
    essa fonte (fallback gracioso — nem toda fonte precisa de skill).

    Args:
        source: nome da fonte (campo `source` da wiki page). Ex: "FINEP", "FAPESP".
        skill_type: tipo de skill — "compliance" (default) ou "writing".
    """
    if not source:
        return ""
    normalized = _normalize_source(source)
    skill_file = _SKILLS_DIR / f"{normalized}_{skill_type}.md"
    if not skill_file.exists():
        logger.debug("skill ausente: %s", skill_file.name)
        return ""
    try:
        return skill_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("falha ao ler skill %s: %s", skill_file, e)
        return ""


def available_skills() -> list[dict]:
    """Lista todas as skills disponíveis em skills/. Útil para debug e UI."""
    if not _SKILLS_DIR.exists():
        return []
    out = []
    for f in sorted(_SKILLS_DIR.glob("*.md")):
        # Convenção: <source>_<type>.md
        parts = f.stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        source, skill_type = parts
        out.append({
            "source": source,
            "type": skill_type,
            "file": f.name,
            "size_bytes": f.stat().st_size,
        })
    return out
