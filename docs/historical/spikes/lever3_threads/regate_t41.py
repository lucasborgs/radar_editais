"""Re-gate T4.1 (throwaway): fam3 x3 treatment-only.

Opção D (governança 2026-07-19): título de seção é ESTRUTURAL → pedido de mudança
de título no chat de escrita = reconhecer e redirecionar ao plano, NUNCA renomear.
Mede 2 casos da família 3, 3 runs cada:
  - v2_familia3_espectra_user_edit      (substitui 1ª frase de CONTEÚDO — eval_user_edit_preserved; baseline 3/3, não pode regredir)
  - v2_familia3_tratorbr_title_redirect (pede mudar TÍTULO → eval_title_redirect: redireciona ao plano)

Ambiente: Postgres LOCAL :54322 (regra absoluta). Rodar do worktree.
Lição #4 do handoff: garante que o `core` importado é o do worktree, não o editable-install.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getcwd())  # worktree > editable-install (lição #4)

from radar.core.environment import load_environment_profile  # noqa: E402

load_environment_profile()

from radar.core.eval.writing import (  # noqa: E402
    eval_title_redirect,
    eval_user_edit_preserved,
    load_data_v2,
    task,
)

FAM3 = {"v2_familia3_espectra_user_edit", "v2_familia3_tratorbr_title_redirect"}


def _evaluate(output, metadata) -> tuple[bool, str]:
    if metadata.get("expect_title_redirect"):
        ev = eval_title_redirect(output=output, metadata=metadata)
    else:
        ev = eval_user_edit_preserved(output=output, metadata=metadata)
    if not ev:
        return False, "sem evaluator"
    return bool(ev["value"]), ev.get("comment", "")


def main() -> int:
    core_mod = sys.modules["core"].__file__
    print(f"[env] core de: {os.path.dirname(os.path.dirname(core_mod))}")
    print(f"[env] DATABASE_URL={os.environ.get('DATABASE_URL')}")
    print(f"[env] EVAL_WORKSPACE_ID={os.environ.get('EVAL_WORKSPACE_ID')}\n")

    items = [it for it in load_data_v2() if it["metadata"]["case_id"] in FAM3]
    print(f"[data] {len(items)} runs (esperado 6 = 2 casos x3)\n")

    tally: dict[str, list[bool]] = {}
    for i, item in enumerate(items, 1):
        cid = item["metadata"]["case_id"]
        try:
            output = task(item=item)
            ok, comment = _evaluate(output, item["metadata"])
            print(
                f"[run {i}/6] {cid}: value={ok} ({comment}) "
                f"saved={output.get('saved')} draft_chars={output.get('draft_chars')}"
            )
            if not ok:
                resp = (output.get("followup_response") or output.get("assistant_text") or "")
                print(f"           resp head: {resp[:200]!r}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[run {i}/6] {cid}: ERRO {type(e).__name__}: {e}")
        tally.setdefault(cid, []).append(ok)

    print("\n=== RESULTADO ===")
    for cid, res in tally.items():
        n_ok = sum(res)
        label = "título/redirect" if "tratorbr" in cid else "frase/conteúdo"
        print(f"  {label:16} ({cid}): {n_ok}/{len(res)}  {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
