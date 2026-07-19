"""Re-gate T4.1 (throwaway): fam3 x3 treatment-only.

Fix de precedência no prefixo colapsado (mecanismo #2). Mede user_edit_preserved
nos 2 casos da família 3, 3 runs cada:
  - v2_familia3_espectra_user_edit  (substitui 1ª frase — NÃO pode regredir; baseline 3/3)
  - v2_familia3_tratorbr_user_edit   (altera título — RESIDUAL; baseline 3/3, treat pré-fix 0/3)

Ambiente: Postgres LOCAL :54322 (regra absoluta). Rodar do worktree.
Lição #4 do handoff: garante que o `core` importado é o do worktree, não o editable-install.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getcwd())  # worktree > editable-install (lição #4)

from core.environment import load_environment_profile  # noqa: E402

load_environment_profile()

from core.eval.writing import (  # noqa: E402
    eval_user_edit_preserved,
    load_data_v2,
    task,
)

FAM3 = {"v2_familia3_espectra_user_edit", "v2_familia3_tratorbr_user_edit"}


def main() -> int:
    core_mod = sys.modules["core"].__file__
    print(f"[env] core de: {os.path.dirname(os.path.dirname(core_mod))}")
    print(f"[env] DATABASE_URL={os.environ.get('DATABASE_URL')}")
    print(f"[env] EVAL_WORKSPACE_ID={os.environ.get('EVAL_WORKSPACE_ID')}")

    items = [it for it in load_data_v2() if it["metadata"]["case_id"] in FAM3]
    print(f"[data] {len(items)} runs (esperado 6 = 2 casos x3)\n")

    tally: dict[str, list[bool]] = {}
    for i, item in enumerate(items, 1):
        cid = item["metadata"]["case_id"]
        try:
            output = task(item=item)
            ev = eval_user_edit_preserved(output=output, metadata=item["metadata"])
            preserved = bool(ev["value"]) if ev else False
            draft = output.get("draft", "") or ""
            intent = item["metadata"].get("edit_intent", "")
            print(
                f"[run {i}/6] {cid}: preserved={preserved} "
                f"saved={output.get('saved')} draft_chars={len(draft)}"
            )
            if not preserved:
                # Diagnóstico: mostra se o intent aparece parcialmente
                print(f"           intent='{intent[:60]}...'")
                print(f"           draft head: {draft[:180]!r}")
        except Exception as e:  # noqa: BLE001
            preserved = False
            print(f"[run {i}/6] {cid}: ERRO {type(e).__name__}: {e}")
        tally.setdefault(cid, []).append(preserved)

    print("\n=== RESULTADO ===")
    for cid, res in tally.items():
        n_ok = sum(res)
        label = "título" if "tratorbr" in cid else "frase"
        print(f"  {label:8} ({cid}): {n_ok}/{len(res)}  {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
