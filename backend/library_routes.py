"""
Endpoints da Content Library.
"""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from typing import Optional

from core.auth import CurrentUserId
from core.content_library import (
    get_workspace_id,
    list_items,
    get_item,
    create_item,
    update_item,
    delete_item,
    search_items,
    extract_text_from_pdf,
    CONTENT_TYPES,
)

router = APIRouter(prefix="/library", tags=["library"])

MAX_PDF_MB = 10


# =============================================================================
# SCHEMAS
# =============================================================================

class CreateItemRequest(BaseModel):
    title: str
    type: str
    content: str
    tags: list[str] = []
    source_url: Optional[str] = None


class UpdateItemRequest(BaseModel):
    title: Optional[str] = None
    type: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    source_url: Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================

def _workspace(user_id: str) -> str:
    return get_workspace_id(user_id)


def _validate_type(type_: str):
    if type_ not in CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo inválido. Use: {', '.join(sorted(CONTENT_TYPES))}",
        )


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("", summary="Lista items da biblioteca")
def library_list(
    user_id: CurrentUserId,
    type: Optional[str] = None,
    q: Optional[str] = None,
):
    workspace_id = _workspace(user_id)
    if q:
        return search_items(workspace_id, q, type_filter=type)
    items = list_items(workspace_id)
    if type:
        items = [i for i in items if i["type"] == type]
    return items


@router.get("/{item_id}", summary="Retorna item completo (com key_facts)")
def library_get(item_id: str, user_id: CurrentUserId):
    workspace_id = _workspace(user_id)
    item = get_item(item_id, workspace_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    return item


@router.post("", status_code=201, summary="Cria item (texto)")
def library_create(req: CreateItemRequest, user_id: CurrentUserId):
    _validate_type(req.type)
    if not req.content.strip():
        raise HTTPException(status_code=422, detail="content não pode estar vazio")
    workspace_id = _workspace(user_id)
    return create_item(
        workspace_id=workspace_id,
        title=req.title,
        type_=req.type,
        content=req.content,
        tags=req.tags,
        source_url=req.source_url,
    )


@router.post("/upload-pdf", status_code=201, summary="Upload de PDF — extrai texto e cria item")
async def library_upload_pdf(
    user_id: CurrentUserId,
    file: UploadFile = File(...),
    title: str = Form(...),
    type: str = Form("technical_doc"),
    tags: str = Form(""),
):
    _validate_type(type)

    content = await file.read()
    if len(content) > MAX_PDF_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"PDF excede {MAX_PDF_MB}MB")

    try:
        text = extract_text_from_pdf(content)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not text.strip():
        raise HTTPException(status_code=422, detail="Não foi possível extrair texto do PDF")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    workspace_id = _workspace(user_id)
    return create_item(
        workspace_id=workspace_id,
        title=title,
        type_=type,
        content=text,
        tags=tag_list,
    )


@router.put("/{item_id}", summary="Atualiza item")
def library_update(item_id: str, req: UpdateItemRequest, user_id: CurrentUserId):
    if req.type:
        _validate_type(req.type)
    workspace_id = _workspace(user_id)
    updated = update_item(item_id, workspace_id, req.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    return updated


@router.delete("/{item_id}", status_code=204, summary="Remove item")
def library_delete(item_id: str, user_id: CurrentUserId):
    workspace_id = _workspace(user_id)
    if not delete_item(item_id, workspace_id):
        raise HTTPException(status_code=404, detail="Item não encontrado")
