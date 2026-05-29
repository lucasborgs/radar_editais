#!/usr/bin/env python3
"""
Eval comparativo do agente WritingSession vs pipeline legado.

SKELETON — pretende rodar como script offline depois que houver shadow log de
N sessões. A infra de shadow log NÃO está nesta PR (Sprint 2d adiou); este
script vive como esqueleto + checklist do que medir quando os dados existirem.

Metodologia esperada:
  1. Selecionar amostra de 20-30 turns reais do legacy (session_turns com
     tool_use IS NULL) cobrindo categorias variadas (pergunta meta, request
     de draft, pergunta sobre prazo/TRL, request de revisão de coerência).
  2. Para cada turn da amostra, reconstruir o estado da sessão NO MOMENTO
     daquele turn (history até N-1, doc_sections, scope).
  3. Re-executar o turn via path agente (`session._turn_agent`) mantendo o
     mesmo input.
  4. Coletar métricas por turn:
       • latência (P50/P95) — agent vs legacy
       • custo (tokens × $/token) — agent vs legacy
       • # de tool calls (apenas agent)
       • diff de assistant_message — agent vs legacy
       • presença de draft persistido (agente: save_draft tool; legacy: <draft>)
       • presença de pending_user_input (apenas agent)
  5. Eval qualitativo manual: revisor humano lê pares (legacy_out, agent_out)
     com a pergunta original e edital, classifica:
       a) "agente claramente melhor"
       b) "empate"
       c) "legacy melhor"
       d) "agente regrediu" (cita o que perdeu)

Critério GO para ramp 50% → 100%:
  ≥ 60% (a) + (b), ≤ 5% (d), P95 latência ≤ 15s, custo médio ≤ 3× legacy.

Uso (quando o shadow log existir):
    python scripts/eval_agent_writing.py --sample 30 --out eval_results.json
    python scripts/eval_agent_writing.py --diff eval_results.json
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=30,
                        help="Tamanho da amostra de turns a re-executar")
    parser.add_argument("--out", type=str, default="eval_results.json",
                        help="Arquivo JSON para salvar resultados")
    parser.add_argument("--diff", type=str,
                        help="Carrega resultado anterior e imprime relatório")
    args = parser.parse_args()

    print("Eval agent vs legacy — SKELETON.", file=sys.stderr)
    print(
        "Pré-requisito: shadow log de turns (não implementado nesta PR).",
        file=sys.stderr,
    )
    print(
        "Args recebidos:",
        f"sample={args.sample}, out={args.out}, diff={args.diff}",
        file=sys.stderr,
    )
    print(
        "TODO: implementar quando agent_writing_enabled estiver ativo em ≥ 5 "
        "workspaces e session_turns.tool_use tiver volume.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
