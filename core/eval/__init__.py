"""Harness de avaliação unificado do Radar de Editais.

Um lugar só para avaliar todos os pipelines (matching, RAG, escrita, …). Cada
pipeline é uma `Suite` que declara:
  • `data`        — casos {input, expected_output, metadata}
  • `task`        — roda o pipeline real sobre um caso → output
  • `evaluators`  — pontuam o output (reaproveitam core/*_eval.py)
  • `run_evaluators` — métricas agregadas da rodada inteira

`run_suite` executa a suíte de dois modos com a MESMA definição:
  • Langfuse configurado → `langfuse.run_experiment` (Datasets+Experiments,
    scores comparáveis entre commits, link na UI).
  • Sem Langfuse        → fallback local idêntico, grava `eval_results/*.json`.

Assim os 3 harnesses antigos (eval_rag/matching/writing) convergem para uma
única superfície sem reescrever a lógica de julgamento. Entrypoint: `python -m
core.eval <suite>`.
"""
from core.eval.harness import Evaluation, Suite, run_suite
from core.eval.registry import SUITES, get_suite

__all__ = ["Evaluation", "Suite", "run_suite", "SUITES", "get_suite"]
