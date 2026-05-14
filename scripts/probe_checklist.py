#!/usr/bin/env python3
"""
Probe do ChecklistService — roda os 3 passes (compliance + quality +
completeness) em paralelo contra um draft sintético.

O draft inclui DELIBERADAMENTE uma inconsistência (que vimos no probe do
Writer — "startups elegíveis" + "apenas ICTs podem apresentar"). O passe
de QUALITY deveria pegar esse tipo de coisa.

Uso:
    python scripts/probe_checklist.py
    python scripts/probe_checklist.py --edital 762
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.checklist_service import auto_review_checklist, build_checklist  # noqa: E402


# Draft com inconsistência deliberada (linhas 9-11) + dado financeiro
# desalinhado (capital de R$50k mas pedindo R$2M — pode disparar flag).
DRAFT = """\
## 1. Objetivo do Projeto

Desenvolveremos uma plataforma de descoberta de enzimas industriais via
machine learning combinada com escala fermentativa em biorreatores de 100L,
visando reduzir a dependência da indústria brasileira de bioprodutos por
enzimas importadas.

## 2. Elegibilidade

A BioFarm Tech Ltda é uma EPP brasileira, pessoa jurídica nacional, com
atividade econômica organizada e intuito lucrativo. Startups e MEs são
elegíveis para esta chamada. No entanto, apenas ICTs podem apresentar
propostas como proponente nesta chamada pública.

## 3. Recursos Solicitados

Solicitaremos R$ 2.000.000,00 em subvenção econômica para custear P&D
interno e aquisição de equipamentos. A empresa possui R$ 50.000,00 de
capital social registrado.
"""

# Outline mínimo que o passe de COMPLETENESS vai comparar contra o draft —
# deve sinalizar as seções faltantes (metodologia, equipe, cronograma, etc.).
OUTLINE_MOCK = [
    "1. Objetivo",
    "2. Elegibilidade",
    "3. Recursos Solicitados",
    "4. Metodologia",
    "5. Equipe Técnica",
    "6. Cronograma",
    "7. Resultados Esperados",
    "8. Indicadores",
]


async def _run(edital_id: str) -> None:
    print(f"[probe] edital_id = {edital_id}")
    print(f"[probe] proposta = {len(DRAFT)} chars, {DRAFT.count('##')} seções")
    print()

    print("[probe] Construindo checklist de requirements via build_checklist()...")
    try:
        requirements = build_checklist(edital_id)
        print(f"[probe] {len(requirements)} requirements carregados")
        if requirements:
            sample = requirements[0]
            print(f"[probe]   amostra: {dict(list(sample.items())[:3])}…")
    except Exception as e:
        print(f"[probe] build_checklist falhou ({e}) — seguindo sem requirements")
        requirements = []

    print()
    print("[probe] Chamando auto_review_checklist (3 passes paralelos)…")
    print()

    result = await auto_review_checklist(
        proposal=DRAFT,
        edital_requirements=requirements,
        outline=OUTLINE_MOCK,
    )

    # ─── COMPLIANCE ───
    print("═" * 80)
    print("  📋 COMPLIANCE")
    print("═" * 80)
    c = result.get("compliance", {})
    print(f"  score: {c.get('score', '—')}")
    issues = c.get("issues") or []
    if not issues:
        print("  (nenhum issue)")
    for i, issue in enumerate(issues, 1):
        if isinstance(issue, dict):
            print(f"  {i}. {issue}")
        else:
            print(f"  {i}. {issue}")

    # ─── QUALITY ───
    print()
    print("═" * 80)
    print("  ✍️  QUALITY")
    print("═" * 80)
    q = result.get("quality", {})
    print(f"  overall_score: {q.get('overall_score', '—')}")
    issues = q.get("issues") or []
    if not issues:
        print("  (nenhum issue)")
    for i, issue in enumerate(issues, 1):
        if isinstance(issue, dict):
            print(f"  {i}. {issue}")
        else:
            print(f"  {i}. {issue}")

    # ─── COMPLETENESS ───
    print()
    print("═" * 80)
    print("  ✅ COMPLETENESS")
    print("═" * 80)
    co = result.get("completeness", {})
    print(f"  overall_score: {co.get('overall_score', '—')}")
    sections = co.get("sections") or []
    if sections:
        print(f"  sections cobertas ({len(sections)}):")
        for s in sections:
            print(f"    {'✓' if (isinstance(s, dict) and s.get('present')) else '·'} {s}")
    missing = co.get("missing_sections") or []
    if missing:
        print(f"  missing_sections ({len(missing)}):")
        for m in missing:
            print(f"    - {m}")

    errors = result.get("error")
    if errors:
        print()
        print("─" * 80)
        print("  ⚠ erros nos passes:")
        for e in errors:
            print(f"  - {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--edital", default="768")
    args = parser.parse_args()
    asyncio.run(_run(args.edital))
    return 0


if __name__ == "__main__":
    sys.exit(main())
