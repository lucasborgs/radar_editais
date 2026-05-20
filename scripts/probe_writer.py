#!/usr/bin/env python3
"""
Probe da integração RAG ↔ WritingSession sem passar pela camada HTTP/auth.

Constrói um WritingSession com service-role + profile mockado, instrumenta
`_call_llm` para capturar as messages enviadas ao LLM, chama `turn()`
com uma query real, e imprime:

  1. Cada mensagem do prompt (system / user / RAG chunks / library context)
  2. A resposta do LLM
  3. Indicação clara se RAG disparou (TRECHOS RELEVANTES DO EDITAL)
  4. Se library auto-retrieval injetou contexto (POSSÍVEL CONTEXTO DA BIBLIOTECA)
  5. draft_content extraído pelo modelo Grantable (se section-hint fornecido)

Uso:
    python scripts/probe_writer.py
    python scripts/probe_writer.py --edital 762 --section-hint "problema"
    python scripts/probe_writer.py --seed-library   # semeia um content_item de teste
    python scripts/probe_writer.py --verbose        # mostra a mensagem inteira (sem truncar)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Tem que vir DEPOIS do load_dotenv pra que LLM_BACKEND/OPENAI_API_KEY sejam vistos.
from core.db import get_supabase_service  # noqa: E402
from core.writing_session import WritingSession  # noqa: E402
from domain.user_profile import CompanyProfile  # noqa: E402

_TEST_EMAIL = "probe@test.local"


def _ensure_test_user(supabase_url: str, service_key: str) -> str:
    """Cria (ou recupera) um user real em auth.users via API admin.

    auth.users tem trigger/constraint complexos — inserir via SQL direto
    quebra. Usar `auth.admin` é o caminho oficial. Operação idempotente
    porque se o email já existe, listamos e retornamos o ID.
    """
    import os

    from supabase import create_client
    sb_admin = create_client(supabase_url, service_key)

    # auth.admin.list_users é paginado; passamos email no query
    try:
        existing = sb_admin.auth.admin.list_users()
        for u in existing or []:
            if getattr(u, "email", None) == _TEST_EMAIL:
                return u.id
    except Exception as e:
        # Loga mas tenta criar mesmo assim — pode ser bug do SDK na listagem.
        print(f"[probe] aviso: list_users falhou ({e}); tentando criar direto.")

    try:
        created = sb_admin.auth.admin.create_user({
            "email": _TEST_EMAIL,
            "password": "test-only-not-secret",
            "email_confirm": True,
        })
        return created.user.id
    except Exception as e:
        # Pode falhar com "already exists" se o list_users teve falsos negativos.
        # Nesse caso re-lista e tenta achar.
        msg = str(e)
        if "already" in msg.lower() or "exists" in msg.lower():
            for u in sb_admin.auth.admin.list_users() or []:
                if getattr(u, "email", None) == _TEST_EMAIL:
                    return u.id
        raise


def _ensure_test_workspace(db, user_id: str) -> str:
    """Retorna o workspace_id de teste, criando se não existe. Idempotente."""
    result = (
        db.table("workspaces")
        .select("id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["id"]

    insert = db.table("workspaces").insert({
        "user_id": user_id,
        "profile": {},
    }).execute()
    return insert.data[0]["id"]


def _mock_profile() -> CompanyProfile:
    """Profile mockado de uma startup de biotecnologia razoável pro edital 768."""
    return CompanyProfile(
        nome="BioFarm Tech Ltda",
        cnpj="12.345.678/0001-90",
        tipo_entidade="empresa",
        tamanho_empresa="EPP",
        one_liner="Desenvolvemos enzimas industriais via ML pra biorrefino mais eficiente.",
        solution_summary=(
            "Plataforma de descoberta de enzimas via machine learning combinada "
            "com escala fermentativa em biorreatores de 100L."
        ),
        descricao_atividades="Pesquisa aplicada em biotecnologia, prototipagem de enzimas, escalonamento.",
        trl=5,
        tipos_financiamento_interesse=["subvencao_nao_reembolsavel"],
    )


def _seed_library_item(db, workspace_id: str) -> None:
    """Insere (idempotente) um content_item de teste no workspace."""
    existing = (
        db.table("content_items")
        .select("id")
        .eq("workspace_id", workspace_id)
        .eq("title", "[probe] Relatório técnico BioFarm 2024")
        .limit(1)
        .execute()
    )
    if existing.data:
        print(f"[probe] library item já existe ({existing.data[0]['id']}) — seed pulado.")
        return

    from core.embedder import embed_query as _embed
    import uuid as _uuid

    item_id = str(_uuid.uuid4())
    text_to_embed = (
        "Relatório técnico BioFarm Tech 2024. "
        "Celulases industriais com +40% atividade. TRL 5 em biorreator 100L. "
        "Parceria USP-ESALQ. P&D = 35% receita."
    )
    print("[probe] gerando embedding do item de teste...")
    vec = _embed(text_to_embed)  # list[float] — supabase-py serializa automaticamente

    result = db.table("content_items").insert({
        "id": item_id,
        "workspace_id": workspace_id,
        "title": "[probe] Relatório técnico BioFarm 2024",
        "type": "technical_doc",
        "content": (
            "Relatório técnico anual BioFarm Tech 2024.\n"
            "Desenvolvemos 3 variantes de celulase com atividade 40% maior que o estado da arte. "
            "TRL 5 confirmado em fermentador de 100L. Parceria com USP-ESALQ em andamento. "
            "CNPJ: 12.345.678/0001-90. Faturamento 2023: R$ 1,2M. P&D: 35% da receita."
        ),
        "summary": "Celulases industriais com ganho de 40% em atividade; TRL 5; parceria USP-ESALQ.",
        "key_facts": [
            "3 variantes de celulase com +40% de atividade",
            "TRL 5 validado em biorreator 100L",
            "Parceria formal com USP-ESALQ",
            "P&D = 35% da receita 2023",
        ],
        "themes": ["biotecnologia", "enzimas", "bioeconomia"],
        "importance_score": 8,
        "enrichment_status": "done",
        "embedding": vec,
    }).execute()
    print(f"[probe] library item criado com embedding: {result.data[0]['id']}")


def _print_message(idx: int, msg: dict, max_chars: int, verbose: bool) -> None:
    role = msg.get("role", "?")
    content = msg.get("content", "") or ""
    is_rag = "TRECHOS RELEVANTES DO EDITAL" in content
    marker = "  📚 RAG CHUNKS" if is_rag else ""
    print(f"\n[{idx}] role={role}  ({len(content)} chars){marker}")
    print("─" * 80)
    if verbose or len(content) <= max_chars:
        print(content)
    else:
        print(content[:max_chars])
        print(f"\n... (+{len(content) - max_chars} chars truncados — use --verbose pra ver tudo)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--edital", default="768",
                        help="ID do edital indexado (default: 768)")
    parser.add_argument("--queries", nargs="+",
                        default=["Quais são os critérios de avaliação e seus pesos?"],
                        help="1+ queries; cada uma vira um turn na MESMA session")
    parser.add_argument("--section-hint", default=None,
                        help="Seção da proposta sendo redigida (opcional)")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostra prompt completo do 1º turn (não só a aba de chunks)")
    parser.add_argument("--max-chars", type=int, default=1500,
                        help="Max chars por message no print quando --verbose")
    parser.add_argument("--seed-library", action="store_true",
                        help="Semeia um content_item de teste no workspace (para exercitar library auto-retrieval)")
    args = parser.parse_args()

    # Logs verbosos do WritingSession (retrieve_chunks, etc).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suprime ruído de bibliotecas barulhentas.
    for noisy in ("httpx", "httpcore", "openai._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    import os
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        print("ERRO: SUPABASE_URL e SUPABASE_SERVICE_KEY são necessários.", file=sys.stderr)
        return 2

    user_id = _ensure_test_user(supabase_url, service_key)
    print(f"[probe] test user_id = {user_id}")

    db = get_supabase_service()
    workspace_id = _ensure_test_workspace(db, user_id)
    print(f"[probe] workspace_id = {workspace_id}")

    profile = _mock_profile()

    if args.seed_library:
        _seed_library_item(db, workspace_id)

    print(f"[probe] construindo WritingSession (edital={args.edital})...")
    session = WritingSession(
        db=db,
        workspace_id=workspace_id,
        profile=profile,
        edital_id=args.edital,
    )
    print(f"[probe] session_id = {session.session_id}")
    print(f"[probe] outline com {len(session._proposal_outline)} seção(ões)")

    # Intercepta `_call_llm` pra capturar as messages enviadas ao LLM.
    captured: dict = {}
    original_call_llm = session._call_llm

    def _capturing_call_llm(messages):
        captured["messages"] = messages
        return original_call_llm(messages)

    session._call_llm = _capturing_call_llm

    for turn_idx, query in enumerate(args.queries, start=1):
        print()
        print("█" * 80)
        print(f"  TURN {turn_idx}/{len(args.queries)} — query: {query!r}")
        if args.section_hint:
            print(f"  section_hint: {args.section_hint!r}")
        print("█" * 80)

        result = session.turn(user_message=query, section_hint=args.section_hint)
        messages = captured.get("messages") or []

        # No primeiro turn (ou se --verbose): mostra o prompt inteiro.
        # Senão: só o sumário do bloco de chunks RAG.
        if turn_idx == 1 and args.verbose:
            print()
            print(f"  PROMPT — {len(messages)} messages enviadas ao LLM")
            print("─" * 80)
            for i, msg in enumerate(messages):
                _print_message(i, msg, args.max_chars, verbose=True)

        import re as _re

        # Sumário dos chunks recuperados (sempre).
        rag_msg = next(
            (m for m in messages if "TRECHOS RELEVANTES DO EDITAL" in (m.get("content") or "")),
            None,
        )
        print()
        if rag_msg:
            print(f"  [RAG] disparou: {len(rag_msg['content'])} chars de chunks no prompt")
            headers = _re.findall(r"\[Trecho \d+ — [^\]]+\][^\n]*", rag_msg["content"] or "")
            for h in headers[:7]:
                print(f"     {h}")
        else:
            print("  [RAG] nao disparou nesse turn.")

        # Indicador de library auto-retrieval.
        lib_context = ""
        for m in messages:
            if "POSSÍVEL CONTEXTO DA BIBLIOTECA" in (m.get("content") or ""):
                lib_context = m["content"]
                break
        if lib_context:
            titles = _re.findall(r"\[[A-Z_]+\]\s*(.+)", lib_context)
            print(f"  [LIB] auto-retrieval injetou {len(lib_context)} chars | itens: {titles}")
        else:
            print("  [LIB] auto-retrieval nao injetou contexto (sem itens ou relevancia baixa).")

        print()
        print("  💬 RESPOSTA DO LLM:")
        print("─" * 80)
        response = result.get("assistant_message") or result.get("response") or ""
        if not response:
            import json as _json
            print(_json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            # Truncamento moderado pra não inundar
            limit = 2000 if not args.verbose else 10_000
            print(response[:limit])
            if len(response) > limit:
                print(f"\n... (+{len(response) - limit} chars — use --verbose pra ver tudo)")

        # Grantable: draft_content auto-salvo na seção.
        draft = result.get("draft_content")
        if draft:
            print()
            print(f"  [GRANTABLE] draft auto-salvo ({len(draft)} chars):")
            print("─" * 80)
            print(draft[:1000])
            if len(draft) > 1000:
                print(f"  ... (+{len(draft) - 1000} chars)")
        elif args.section_hint:
            print()
            print("  [GRANTABLE] LLM nao gerou <draft> nesse turn (sem auto-save).")

        if not result.get("success", True):
            print(f"\n  ⚠ erro: {result.get('error')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
