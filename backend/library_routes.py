"""
Endpoints da Content Library.
"""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.auth import CurrentUserId, DbClient
from core.content_library import (
    CONTENT_TYPES,
    SUPPORTED_UPLOAD_EXTS,
    archive_item,
    create_item,
    delete_item,
    extract_document_text,
    get_item,
    get_workspace_id,
    list_items,
    search_items,
    unarchive_item,
    update_item,
)

router = APIRouter(prefix="/library", tags=["library"])

MAX_UPLOAD_MB = 10


# =============================================================================
# SCHEMAS
# =============================================================================

class CreateItemRequest(BaseModel):
    title: str
    type: str
    content: str
    tags: list[str] = []
    source_url: str | None = None


class UpdateItemRequest(BaseModel):
    title: str | None = None
    type: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    source_url: str | None = None


# =============================================================================
# HELPERS
# =============================================================================

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
    db: DbClient,
    type: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
):
    workspace_id = get_workspace_id(db, user_id)
    if q:
        return search_items(db, workspace_id, q, type_filter=type)
    items = list_items(db, workspace_id, include_archived=include_archived)
    if type:
        items = [i for i in items if i["type"] == type]
    return items


@router.get("/recommend", summary="Recupera items mais relevantes via scoring multi-critério (Fase 2 #16)")
def library_recommend(
    user_id: CurrentUserId,
    db: DbClient,
    q: str,
    k: int = 10,
):
    """Multi-criteria retrieval: 0.4·recency + 0.3·importance(decay) + 0.3·cosine.

    Usado para autocomplete de @ mentions (substitui o ilike simples quando
    embeddings estão populados) e seleção automática de library_items para
    WritingSession. Items sem embedding ainda gerado são silenciosamente
    excluídos — retorna lista possivelmente menor que k até backfill completar.
    """
    workspace_id = get_workspace_id(db, user_id)
    from core.retrieval.retriever import retrieve_library_items
    return retrieve_library_items(workspace_id, query=q, k=k)


@router.get("/{item_id}", summary="Retorna item completo (com key_facts)")
def library_get(item_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    item = get_item(db, item_id, workspace_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    return item


@router.post("", status_code=201, summary="Cria item (texto)")
async def library_create(req: CreateItemRequest, user_id: CurrentUserId, db: DbClient):
    _validate_type(req.type)
    if not req.content.strip():
        raise HTTPException(status_code=422, detail="content não pode estar vazio")
    workspace_id = get_workspace_id(db, user_id)
    return await create_item(
        db,
        workspace_id=workspace_id,
        title=req.title,
        type_=req.type,
        content=req.content,
        tags=req.tags,
        source_url=req.source_url,
    )


@router.post("/upload-pdf", status_code=201,
             summary="Upload de documento (PDF/DOCX/TXT/MD) — extrai texto e cria item")
async def library_upload_pdf(
    user_id: CurrentUserId,
    db: DbClient,
    file: UploadFile = File(...),
    title: str = Form(...),
    type: str = Form("technical_doc"),
    tags: str = Form(""),
):
    _validate_type(type)

    content = await file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Arquivo excede {MAX_UPLOAD_MB}MB")

    try:
        text = extract_document_text(content, file.filename or "")
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail=f"Não foi possível extrair texto ({', '.join(SUPPORTED_UPLOAD_EXTS)}).",
        )

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    workspace_id = get_workspace_id(db, user_id)
    return await create_item(
        db,
        workspace_id=workspace_id,
        title=title,
        type_=type,
        content=text,
        tags=tag_list,
    )


@router.put("/{item_id}", summary="Atualiza item")
async def library_update(item_id: str, req: UpdateItemRequest, user_id: CurrentUserId, db: DbClient):
    if req.type:
        _validate_type(req.type)
    workspace_id = get_workspace_id(db, user_id)
    updated = await update_item(db, item_id, workspace_id, req.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    return updated


@router.delete("/{item_id}", status_code=204, summary="Remove item")
def library_delete(item_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    if not delete_item(db, item_id, workspace_id):
        raise HTTPException(status_code=404, detail="Item não encontrado")


@router.post("/{item_id}/archive", status_code=204, summary="Arquiva item (soft-delete)")
def library_archive(item_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    if not archive_item(db, item_id, workspace_id):
        raise HTTPException(status_code=404, detail="Item não encontrado ou já arquivado")


@router.post("/{item_id}/unarchive", status_code=204, summary="Desarquiva item")
def library_unarchive(item_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    if not unarchive_item(db, item_id, workspace_id):
        raise HTTPException(status_code=404, detail="Item não encontrado")
