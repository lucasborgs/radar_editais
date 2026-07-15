"""Harness de avaliação unificado do Radar de Editais.

Um lugar só para avaliar todos os pipelines (matching, RAG, escrita, …). Cada
pipeline é uma `Suite` que declara:
  • `data`        — casos {input, expected_output, metadata}
  • `task`        — roda o pipeline real sobre um caso → output
  • `evaluators`  — pontuam o output (reaproveitam core/*_eval.py)
  • `run_evaluators` — métricas agregadas da rodada inteira

`run_suite` executa a MESMA definição com duas intenções:
  • `run`  → diagnóstico, local por padrão;
  • `gate` → decisão por critérios aceitos, sempre completa.

Todo resultado grava um manifesto local em `eval_results/*.json`; publicação no
Langfuse é explícita. Entrypoint: `python -m core.eval run <suite>` ou
`python -m core.eval gate <suite>`.
"""
from core.eval.harness import Criterion, Evaluation, Suite, run_suite
from core.eval.registry import SUITES, get_suite

__all__ = ["Criterion", "Evaluation", "Suite", "run_suite", "SUITES", "get_suite"]
