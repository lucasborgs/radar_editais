"""Kernel read-only de evidência factual para o Explorar."""
from __future__ import annotations

import re

from core.retrieval.retriever import retrieve_chunks

_SECTION_TOKEN_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)+)\.?")


def _coverage_outline(chunks: list[dict]) -> list[str]:
    """Grupos normativos da família expandida, para checklist de síntese."""
    groups: list[str] = []
    for index, chunk in enumerate(chunks):
        if index and not chunk.get("structural_expansion"):
            continue
        tokens = _SECTION_TOKEN_RE.findall(chunk.get("section") or "")
        if not tokens:
            continue
        parts = tokens[-1].split(".")
        group = ".".join(parts[:2]) if len(parts) >= 2 else parts[0]
        if group not in groups:
            groups.append(group)
    return groups


def retrieve_edital_evidence(
    edital_id: str,
    query: str,
    *,
    profile: str = "factual_point",
) -> list[dict]:
    """Recupera somente o edital-alvo, com cobertura estrutural quando pedida."""
    enumerative = profile == "factual_enumerative"
    return retrieve_chunks(
        None,
        [edital_id],
        query,
        k=6 if enumerative else 5,
        max_per_source=0 if enumerative else 2,
        hyde=not enumerative,
        expand_sections=enumerative,
        # Critérios de admissibilidade costumam ter dezenas de subitens. O
        # limite anterior (16) cortava a seção 4 da FAPESC ainda em 4.2,
        # antes de coordenador, equipe e proposta (4.3–4.5).
        expansion_limit=36,
    )


def format_factual_evidence(chunks: list[dict], *, char_cap: int = 40_000) -> str:
    """Formata evidência com proveniência suficiente para citação pelo agente."""
    if not chunks:
        return "Nenhuma evidência vigente foi encontrada para esta pergunta."
    groups = _coverage_outline(chunks)
    outline = ""
    if groups:
        outline = (
            "[CHECKLIST DE COBERTURA ENUMERATIVA]\n"
            "Antes de responder, cubra separadamente todos os grupos materiais "
            f"recuperados: {', '.join(groups)}. Não pare no primeiro grupo.\n\n"
        )
    parts: list[str] = []
    used = len(outline)
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        header = (
            f"[EVIDÊNCIA {chunk.get('id')}] edital={chunk.get('edital_id')} | "
            f"documento={chunk.get('source_file') or 'desconhecido'} | "
            f"versão={meta.get('revision', 'não informada')} | "
            f"data={meta.get('published_at') or 'não informada'} | "
            f"autoridade={meta.get('authority_state', 'não informada')} | "
            f"ordem_composição={meta.get('composition_order', 'não informada')} | "
            f"seção={chunk.get('section') or 'não informada'} | "
            f"página={chunk.get('page_range') or 'não informada'} | "
            f"url={meta.get('source_url') or 'não informada'}"
        )
        text = (chunk.get("text") or "").strip()
        piece = f"{header}\n{text}\n"
        remaining = char_cap - used
        if remaining <= len(header) + 100:
            break
        if len(piece) > remaining:
            piece = piece[:remaining] + "\n[conteúdo truncado pelo limite da tool]\n"
        parts.append(piece)
        used += len(piece)
    # O checklist é metadado produzido pelo sistema, não conteúdo do PDF. Ele
    # precisa ficar fora de <dados_externos>, cujo contrato manda o agente
    # ignorar qualquer instrução por segurança contra prompt injection.
    return outline + "<dados_externos>\n" + "\n".join(parts) + "</dados_externos>"
