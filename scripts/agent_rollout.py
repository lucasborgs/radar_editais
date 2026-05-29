#!/usr/bin/env python3
"""
Rollout do agente WritingSession por workspace (Sprint 2 do Cenário B).

Wraps SQL UPDATE em workspaces.agent_writing_enabled — usado durante o ramp
gradual (10% → 50% → 100%) e para ativação manual em dev/staging.

Uso:
    # Ver status atual de um workspace
    python scripts/agent_rollout.py status <workspace_id>

    # Ativar agente num workspace específico
    python scripts/agent_rollout.py enable <workspace_id>

    # Desativar (rollback)
    python scripts/agent_rollout.py disable <workspace_id>

    # Listar workspaces com agente ativado
    python scripts/agent_rollout.py list-enabled

    # Sumário: quantos workspaces ativados vs total
    python scripts/agent_rollout.py summary

Não há comando "enable-percentage" deliberadamente: ramp por % expõe
workspaces aleatoriamente, o que é ruim de auditar. Prefira ativar
nomeadamente os workspaces dos próximos testers e crescer manualmente.

O service-role do Supabase é usado (lê SUPABASE_SERVICE_KEY).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import get_supabase_service  # noqa: E402


def _client():
    return get_supabase_service()


def cmd_status(workspace_id: str) -> int:
    db = _client()
    result = (
        db.table("workspaces")
        .select("id, name, agent_writing_enabled, agent_explore_enabled")
        .eq("id", workspace_id)
        .maybe_single()
        .execute()
    )
    row = result.data if result else None
    if not row:
        print(f"Workspace '{workspace_id}' não encontrado.", file=sys.stderr)
        return 1
    print(f"workspace_id:           {row['id']}")
    print(f"name:                   {row.get('name', '(sem nome)')}")
    print(f"agent_writing_enabled:  {row['agent_writing_enabled']}")
    print(f"agent_explore_enabled:  {row['agent_explore_enabled']}")
    return 0


def cmd_enable(workspace_id: str) -> int:
    return _toggle(workspace_id, True)


def cmd_disable(workspace_id: str) -> int:
    return _toggle(workspace_id, False)


def _toggle(workspace_id: str, value: bool) -> int:
    db = _client()
    result = (
        db.table("workspaces")
        .update({"agent_writing_enabled": value})
        .eq("id", workspace_id)
        .execute()
    )
    if not result.data:
        print(f"Workspace '{workspace_id}' não encontrado.", file=sys.stderr)
        return 1
    state = "ATIVADO" if value else "DESATIVADO"
    print(f"agent_writing_enabled = {value} ({state}) para workspace {workspace_id}")
    return 0


def cmd_list_enabled() -> int:
    db = _client()
    result = (
        db.table("workspaces")
        .select("id, name, created_at")
        .eq("agent_writing_enabled", True)
        .order("created_at", desc=False)
        .execute()
    )
    rows = result.data or []
    if not rows:
        print("Nenhum workspace com agent_writing_enabled = true.")
        return 0
    print(f"{len(rows)} workspaces com agente ativado:")
    for r in rows:
        print(f"  {r['id']:>40}  {r.get('name', '(sem nome)')}  ({r.get('created_at', '?')})")
    return 0


def cmd_summary() -> int:
    db = _client()
    total = db.table("workspaces").select("id", count="exact").execute()
    enabled = (
        db.table("workspaces")
        .select("id", count="exact")
        .eq("agent_writing_enabled", True)
        .execute()
    )
    n_total = total.count or 0
    n_enabled = enabled.count or 0
    pct = (n_enabled / n_total * 100.0) if n_total else 0.0
    print(f"Total workspaces:    {n_total}")
    print(f"Com agente ativo:    {n_enabled} ({pct:.1f}%)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rollout do agente WritingSession por workspace.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="Ver estado de um workspace")
    s.add_argument("workspace_id")

    e = sub.add_parser("enable", help="Ativar agent_writing_enabled")
    e.add_argument("workspace_id")

    d = sub.add_parser("disable", help="Desativar agent_writing_enabled (rollback)")
    d.add_argument("workspace_id")

    sub.add_parser("list-enabled", help="Listar workspaces com agente ativado")
    sub.add_parser("summary", help="Sumário do rollout")

    args = parser.parse_args()
    if args.cmd == "status":
        return cmd_status(args.workspace_id)
    if args.cmd == "enable":
        return cmd_enable(args.workspace_id)
    if args.cmd == "disable":
        return cmd_disable(args.workspace_id)
    if args.cmd == "list-enabled":
        return cmd_list_enabled()
    if args.cmd == "summary":
        return cmd_summary()
    return 1


if __name__ == "__main__":
    sys.exit(main())
