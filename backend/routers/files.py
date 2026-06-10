"""File tree do workspace em Supabase Storage (Fase 3 #22)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.auth import CurrentUserId, DbClient
from core.content_library import get_workspace_id

router = APIRouter(tags=["files"])


@router.get("/files", summary="Lista arquivos do workspace no storage (file tree)")
def files_list(user_id: CurrentUserId, db: DbClient, prefix: str | None = None):
    """File tree do workspace. RLS filtra automaticamente para o workspace do user.

    Path estrutura: <workspace_id>/<session_id>/<filename>. Se `prefix` for None,
    lista tudo dentro do workspace; se for "<session_id>", lista só dessa sessão.
    """
    workspace_id = get_workspace_id(db, user_id)
    base_prefix = f"{workspace_id}/" if prefix is None else f"{workspace_id}/{prefix}"
    try:
        result = db.storage.from_("proposals").list(base_prefix)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Falha ao listar storage: {e}",
        ) from e
    return {"prefix": base_prefix, "files": result}


@router.get("/files/signed-url", summary="Gera signed URL para download de um arquivo")
def files_signed_url(path: str, user_id: CurrentUserId, db: DbClient, expires_in: int = 3600):
    """Retorna signed URL com TTL. RLS no storage protege contra cross-workspace access."""
    workspace_id = get_workspace_id(db, user_id)
    if not path.startswith(f"{workspace_id}/"):
        # Defense-in-depth — RLS já protege, mas falhamos cedo.
        raise HTTPException(status_code=403, detail="Path fora do workspace")
    try:
        signed = db.storage.from_("proposals").create_signed_url(path, expires_in)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Falha ao gerar signed URL: {e}",
        ) from e
    return {"path": path, "signed_url": signed.get("signedURL"), "expires_in": expires_in}
