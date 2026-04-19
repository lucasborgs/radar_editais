"""
Content Library — operações CRUD e extração de fatos atômicos via LLM.

Cada ContentItem pertence a um workspace. Os campos `summary`, `key_facts`
e `themes` são gerados por LLM no momento do upload (ou manualmente via /enrich).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from core.db import get_supabase

logger = logging.getLogger(__name__)

CONTENT_TYPES = {"proposal", "project_description", "team_bio", "technical_doc", "other"}

_ENRICH_SYSTEM = """Você é um especialista em análise de documentos de inovação e P&D.
Extraia informações estruturadas do documento abaixo. Responda APENAS com JSON válido."""

_ENRICH_USER = """Documento:
\"\"\"
{content}
\"\"\"

Extraia e retorne:
{{
  "summary": "Resumo em 3 frases do que este documento trata.",
  "key_facts": [
    "Fato atômico 1 relevante para uma proposta de P&D",
    "Fato atômico 2",
    "..."
  ],
  "themes": ["tema1", "tema2", "tema3"]
}}

key_facts deve ter entre 5 e 15 itens. Cada fato deve ser autocontido (não usar pronomes como 'eles', 'a empresa').
themes deve ter entre 2 e 6 termos técnicos ou setoriais."""


# =============================================================================
# LLM
# =============================================================================

def _make_client():
    from openai import OpenAI
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        return OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"]), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def enrich_content(content: str) -> dict:
    """Chama LLM para extrair summary, key_facts e themes de um texto."""
    client, model = _make_client()
    truncated = content[:8000]  # evitar tokens excessivos
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _ENRICH_SYSTEM},
                {"role": "user", "content": _ENRICH_USER.format(content=truncated)},
            ],
            temperature=0.1,
            max_tokens=1500,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        data = json.loads(raw)
        return {
            "summary": data.get("summary", ""),
            "key_facts": data.get("key_facts", []),
            "themes": data.get("themes", []),
        }
    except Exception as e:
        logger.error("Erro ao enriquecer conteúdo: %s", e)
        return {"summary": "", "key_facts": [], "themes": []}


# =============================================================================
# PDF EXTRACTION
# =============================================================================

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrai texto de um PDF via pypdf."""
    try:
        import pypdf
        reader = pypdf.PdfReader(BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p for p in pages if p.strip())
    except ImportError:
        raise RuntimeError("pypdf não instalado. Execute: pip install pypdf")
    except Exception as e:
        raise RuntimeError(f"Falha ao extrair texto do PDF: {e}")


# =============================================================================
# CRUD
# =============================================================================

def get_workspace_id(user_id: str) -> str:
    """Retorna ou cria workspace para o user_id."""
    db = get_supabase()
    result = db.table("workspaces").select("id").eq("user_id", user_id).maybe_single().execute()
    if result.data:
        return result.data["id"]
    created = db.table("workspaces").insert({"user_id": user_id, "profile": {}}).execute()
    return created.data[0]["id"]


def list_items(workspace_id: str) -> list[dict]:
    db = get_supabase()
    result = (
        db.table("content_items")
        .select("id, title, type, tags, summary, themes, created_at, updated_at")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def get_item(item_id: str, workspace_id: str) -> Optional[dict]:
    db = get_supabase()
    result = (
        db.table("content_items")
        .select("*")
        .eq("id", item_id)
        .eq("workspace_id", workspace_id)
        .maybe_single()
        .execute()
    )
    return result.data


def create_item(
    workspace_id: str,
    title: str,
    type_: str,
    content: str,
    tags: list[str],
    source_url: Optional[str] = None,
    enrich: bool = True,
) -> dict:
    enriched = enrich_content(content) if enrich else {"summary": "", "key_facts": [], "themes": []}
    db = get_supabase()
    result = db.table("content_items").insert({
        "id": str(uuid.uuid4()),
        "workspace_id": workspace_id,
        "title": title,
        "type": type_,
        "content": content,
        "source_url": source_url,
        "tags": tags,
        "summary": enriched["summary"],
        "key_facts": enriched["key_facts"],
        "themes": enriched["themes"],
    }).execute()
    return result.data[0]


def update_item(item_id: str, workspace_id: str, updates: dict) -> Optional[dict]:
    allowed = {"title", "type", "content", "tags", "source_url"}
    payload = {k: v for k, v in updates.items() if k in allowed}
    if not payload:
        return get_item(item_id, workspace_id)

    if "content" in payload:
        enriched = enrich_content(payload["content"])
        payload.update(enriched)

    db = get_supabase()
    result = (
        db.table("content_items")
        .update(payload)
        .eq("id", item_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    return result.data[0] if result.data else None


def delete_item(item_id: str, workspace_id: str) -> bool:
    db = get_supabase()
    result = (
        db.table("content_items")
        .delete()
        .eq("id", item_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    return bool(result.data)


def search_items(workspace_id: str, query: str, type_filter: Optional[str] = None) -> list[dict]:
    """Busca simples por ilike no título, summary e tags."""
    db = get_supabase()
    q = (
        db.table("content_items")
        .select("id, title, type, tags, summary, themes, created_at")
        .eq("workspace_id", workspace_id)
        .ilike("title", f"%{query}%")
    )
    if type_filter:
        q = q.eq("type", type_filter)
    result = q.order("created_at", desc=True).execute()
    return result.data or []
