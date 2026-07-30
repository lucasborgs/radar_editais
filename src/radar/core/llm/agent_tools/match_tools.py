"""Tools de match do ExploreAgent/WritingSession — motor v3 (Fase 2).

Embrulham `radar.core.services.match_v3`: o lado empresa vem de `company_chunks`
(workspace autenticado, refresh on-demand) ou do perfil efêmero (explore
público). O match é texto real × texto real (chunks) — sem extração de nós,
sem LLM no ranking. Só registradas quando há PERFIL (o explore público
stateless não as vê).

Nomes das tools preservados (`find_matching_editais`/`find_matching_entities`)
— o sinal `called_match` do explore e o filtro da WritingSession dependem deles.
"""
from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)

_BRIEF_HINT = (
    " Cards com o detalhe completo (status, prazo, valor, justificativa) já "
    "aparecem na tela — NÃO repita isso na resposta, comente em no máximo 2 frases."
)


def _format_matches(matches: list, *, brief: bool = False) -> str:
    if brief:
        names = "; ".join(f"{m.source}/{m.edital_id} — {m.name[:60]}" for m in matches)
        return f"{len(matches)} editais com afinidade ao perfil: {names}.{_BRIEF_HINT}"
    lines = [f"{len(matches)} editais com afinidade ao perfil (match por conteúdo, mais fortes primeiro):"]
    for m in matches:
        disp = [x for x in (m.status, f"prazo {m.prazo}" if m.prazo else "", m.valor) if x]
        meta = f"  ({' | '.join(disp)})" if disp else ""
        lines.append(f"\n• {m.source}/{m.edital_id} — {m.name[:80]}{meta}")
        for x in m.matched_excerpts[:2]:
            lines.append(
                f"    porquê: «{x['company_text'][:90]}» ↔ «{x['edital_text'][:90]}» ({x['score']:.2f})"
            )
    lines.append("\n(Afinidade temática, não elegibilidade dura — quem decide é o usuário.)")
    return "\n".join(lines)


def _format_entity_matches(matches: list[dict], *, brief: bool = False) -> str:
    if not matches:
        return "Nenhum investidor/programa com afinidade suficiente ao perfil."
    if brief:
        names = "; ".join(f"[{m['kind']}] {m['name'][:50]}" for m in matches)
        return f"{len(matches)} entidades com afinidade ao perfil: {names}.{_BRIEF_HINT}"
    lines = [f"{len(matches)} entidades (investidor/programa) com afinidade ao perfil:"]
    for m in matches:
        desc = f" — {m['description'][:80]}" if m.get("description") else ""
        lines.append(f"\n• [{m['kind']}] {m['name'][:70]}{desc}")
        for x in (m.get("matched_excerpts") or [])[:2]:
            lines.append(
                f"    porquê: «{x['company_text'][:90]}» ↔ «{x['edital_text'][:90]}» ({x['score']:.2f})"
            )
    lines.append("\n(Afinidade temática — parceria/capital em potencial, não elegibilidade dura.)")
    return "\n".join(lines)


def build_match_tools(
    profile_text: str,  # noqa: ARG001 — mantido na assinatura p/ compat; o v3 usa `profile`
    *,
    profile: dict | None = None,
    workspace_id: str | None = None,
    db=None,
    brief: bool = False,
) -> list[BaseTool]:
    """Tools de match para o caminho COM perfil. `profile` (dict estruturado) é
    capturado por closure — duas sessões nunca compartilham perfil; alimenta o
    lado empresa (chunks) E o Stage 1 (elegibilidade — unsat some, unknown fica).

    `workspace_id`+`db` (autenticado): o lado empresa vem de `company_chunks`
    (perfil + library, refresh on-demand). Sem eles (explore público), o perfil
    do request vira chunks efêmeros (cache in-process por hash).

    `brief`: True quando o caller já renderiza os resultados como cards em UI
    (ExploreAgent) — o RESULTADO da tool não carrega o detalhe, só nome+id, para
    o agente literalmente não ter o que repetir (fix estrutural validado)."""
    from radar.core.services import match_v3

    @tool
    def find_matching_editais(top_k: int = 8) -> str:
        """[MATCH COM PERFIL] Encontra editais que COMBINAM COM O PERFIL DA EMPRESA.

        USE SOMENTE quando o usuário TEM perfil preenchido e pergunta "quais editais
        servem para mim?", "o que tem para a minha empresa?". Retorna matches
        ranqueados por afinidade. NÃO USE para listar catálogo — use list_editais
        para isso.

        Args:
            top_k: máximo de resultados (default 8).
        """
        try:
            matches = match_v3.find_matching_opportunities(
                profile, workspace_id=workspace_id, db=db,
                kinds=frozenset({"edital"}), top_k=int(top_k),
            )
        except Exception as e:  # noqa: BLE001 — erro-como-string (padrão das tools)
            return f"Erro no match: {e}."
        if not matches:
            return (
                "Nenhum edital com afinidade suficiente ao perfil. Refine o perfil "
                "(mais detalhe sobre o que a empresa faz) ou adicione documentos."
            )
        return _format_matches(matches, brief=brief)

    @tool
    def find_matching_entities(kind: str = "", top_k: int = 8) -> str:
        """[MATCH COM PERFIL] Encontra investidores e programas que COMBINAM COM O PERFIL DA EMPRESA.

        USE SOMENTE quando o usuário TEM perfil preenchido e pergunta "que fundos
        combinam conosco?", "o que serve para minha empresa?". Retorna entidades
        ranqueadas por afinidade com o perfil. NÃO USE para listar catálogo —
        use list_investidores ou list_editais para isso.

        Args:
            kind: filtra o tipo — "investidor" (fundos), "programa" ou "" (ambos).
            top_k: máximo de entidades a retornar (default 8).
        """
        kind_map = {
            "investidor": "investidor", "investidores": "investidor", "investor": "investidor",
            "fundo": "investidor", "fundos": "investidor",
            "programa": "programa", "programas": "programa",
        }
        k = kind_map.get((kind or "").strip().lower(), "")
        out: list[dict] = []
        try:
            if k in ("", "programa"):
                out += [
                    m.to_dict() for m in match_v3.find_matching_opportunities(
                        profile, workspace_id=workspace_id, db=db,
                        kinds=frozenset({"programa"}), top_k=int(top_k),
                    )
                ]
            if k in ("", "investidor"):
                out += [
                    m.to_dict() for m in match_v3.find_matching_investors(
                        profile, workspace_id=workspace_id, db=db, top_k=int(top_k),
                    )
                ]
        except Exception as e:  # noqa: BLE001
            return f"Erro no match de entidades: {e}."
        out.sort(key=lambda m: m.get("affinity", 0), reverse=True)
        return _format_entity_matches(out[: int(top_k)], brief=brief)

    return [find_matching_editais, find_matching_entities]
