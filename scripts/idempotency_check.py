#!/usr/bin/env python3
"""Gera comparação de duas passagens read-only sobre snapshots JSON.

Uso: salvar o snapshot antes/depois de duas passagens equivalentes e executar
`python scripts/idempotency_check.py before.json after.json`. A segunda passagem
deve registrar zero chamadas para conteúdo inalterado no relatório do produtor.
"""
import json
import sys

from radar.core.eval.idempotency import compare_snapshots

if len(sys.argv) != 3:
    raise SystemExit("uso: idempotency_check.py BEFORE.json AFTER.json")
with open(sys.argv[1], encoding="utf-8") as f:
    before = json.load(f)
with open(sys.argv[2], encoding="utf-8") as f:
    after = json.load(f)
print(json.dumps(compare_snapshots(before, after), ensure_ascii=False, indent=2, sort_keys=True))
