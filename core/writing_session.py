"""
Writing Session (Radar de Editais) — NotebookLM style, DB-backed.

Persistência (Wave 2 Track A — ADR B1):
  - Header `writing_sessions` (id, workspace_id, edital_id, status, summary,
    proposal_outline, section_drafts, timestamps)
  - Turnos `session_turns` (1 row por mensagem, com role/section_hint)
  - Estado totalmente recuperado do Postgres a cada request — sessões funcionam
    em deploy multi-instância e sobrevivem a restart.

Fluxo:
  1. __init__: lê (ou cria) a row em writing_sessions, carrega turnos e estado.
               Documentos do edital (PDFs) e library_items são re-derivados em
               disco/Supabase a cada construção — não persistimos esse contexto.
               O outline da proposta vem do DB se já estiver salvo; senão,
               wiki page; senão, LLM (1 chamada).
  2. turn: Writer LLM recebe prefixo estático (perfil + documentos) + histórico
           + mensagem. Cada turno produz dois INSERTs em session_turns
           (user, assistant). A compressão de histórico, quando dispara,
           atualiza writing_sessions.summary.

Prompt caching:
  - Gemini Flash: context caching nativo
  - gpt-4o-mini: caching automático para prompts > 1024 tokens
  O prefixo [system + perfil + documentos] deve sempre vir primeiro e permanecer
  idêntico entre turnos para que o cache seja aproveitado.

Gerenciamento de histórico:
  - Mantém os últimos HISTORY_WINDOW turnos verbatim em memória do request
  - Após COMPRESS_THRESHOLD turnos, os mais antigos viram resumo persistido em
    writing_sessions.summary
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import requests

from config import FINEP_PDFS_DIR, KG_WIKI_DIR
from core.content_library import get_item, mark_items_referenced
from core.embedder import embed_query
from core.reflection_service import load_active_insights
from core.retriever import (
    format_chunks_for_prompt,
    retrieve_chunks,
    retrieve_library_items,
)
from domain.user_profile import CompanyProfile
from supabase import Client

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LLM_BACKEND    = os.getenv("LLM_BACKEND", "openai")
OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

HISTORY_WINDOW     = 6
COMPRESS_THRESHOLD = 10

# Retrieval automático da biblioteca por turno (Fase 2 #16). Quantos items
# injetar e o piso de similaridade (cosine) abaixo do qual o match é
# considerado ruído e descartado — evita despejar contexto irrelevante
# quando a biblioteca não tem nada a ver com a pergunta.
LIBRARY_RETRIEVAL_K   = 3
LIBRARY_RELEVANCE_MIN = 0.25

# @ mentions: usuário pode referenciar items da library no input com @<uuid>.
# O resolver injeta o conteúdo do item como contexto adicional e atualiza
# last_referenced_at para alimentar a fórmula de decay (ADR B4).
_MENTION_RE = re.compile(
    r"@([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

# Grantable: o LLM envolve rascunhos completos em <draft>...</draft>. O texto
# dentro da tag é auto-salvo na seção ativa; o resto vira mensagem de chat.
_DRAFT_RE = re.compile(r"<draft>(.*?)</draft>", re.IGNORECASE | re.DOTALL)


def _extract_draft(text: str) -> tuple[str, str | None]:
    """Separa o conteúdo de <draft>...</draft> do texto de chat.

    Retorna (texto_sem_tag, conteúdo_do_draft | None). Se não houver tag, o
    texto volta intacto e o draft é None.
    """
    match = _DRAFT_RE.search(text)
    if not match:
        return text, None
    draft = match.group(1).strip()
    clean = _DRAFT_RE.sub("", text).strip()
    return clean, (draft or None)

# PDFs a ignorar (deve permanecer sincronizado com `core.tasks._SKIP_KEYWORDS`).
# faq e tabela_com_requisitos foram removidos em 2026-05-13 — vide tasks.py.
_SKIP_KEYWORDS = [
    "minuta", "declaracao", "carta_de_manifestacao",
    "apresentacao", "resultado", "oficio", "telas_fap",
    "orientacoes_para_apresentacao",
    "orientacoes_para_despesas", "relatorio_parcial", "ebook",
]

# =============================================================================
# PROMPTS
# =============================================================================

OUTLINE_SYSTEM = """Você é um especialista em propostas para editais de fomento no Brasil.
Com base no edital abaixo, gere o outline das seções que a proposta deve conter.
Retorne APENAS um JSON array de strings com os títulos das seções, na ordem correta.
Exemplo: ["1. Identificação da empresa", "2. Objeto do projeto", "3. Justificativa"]"""

WRITER_SYSTEM = """Você é um especialista em redação de propostas para editais de fomento no Brasil.
Seu papel é ajudar o usuário a escrever uma proposta técnica de alta qualidade.

Diretrizes:
- Baseie-se nas informações do edital e no perfil da empresa fornecidos.
- Use Markdown para estruturar o texto quando produzir trechos da proposta.
- Quando produzir um trecho, seja propositivo: não diga "poderíamos fazer", diga "faremos".
- Nunca invente dados numéricos que não estejam no perfil ou no edital.
- Use [COMPLETAR: descrição] para lacunas que dependem de informação do usuário.
- Quando uma seção ativa for indicada, concentre a resposta nessa seção.
- Quando uma seção ativa estiver indicada e você produzir um rascunho completo para ela,
  envolva o rascunho em <draft>...</draft>. Use no máximo uma tag <draft> por resposta.
  O conteúdo dentro de <draft> é salvo automaticamente no documento; explicações,
  comentários e perguntas ficam fora da tag."""

COMPRESS_SYSTEM = """Resuma os turnos abaixo em um parágrafo conciso (máx. 200 palavras).
Preserve: decisões tomadas, trechos aprovados pelo usuário e informações adicionais fornecidas.
Responda apenas com o resumo."""


# =============================================================================
# WRITING SESSION
# =============================================================================

class WritingSession:
    """
    Sessão de escrita no estilo NotebookLM, persistida em Postgres.

    Construa com `session_id` para retomar uma sessão existente, ou com
    `edital_id` para criar uma nova. O cliente Supabase recebido deve estar
    autenticado com o JWT do usuário (RLS garante isolamento por workspace).
    """

    def __init__(
        self,
        db: Client,
        workspace_id: str,
        profile: CompanyProfile,
        session_id: str | None = None,
        edital_id: str | None = None,
        llm_backend: str | None = None,
        model: str | None = None,
        library_items: list[dict] | None = None,
    ):
        self._db = db
        self.workspace_id = workspace_id
        self.profile = profile
        self.backend = llm_backend or LLM_BACKEND
        self.model = model or (OLLAMA_MODEL if self.backend == "ollama" else OPENAI_MODEL)

        self._history: list[dict] = []
        self._history_summary: str = ""
        self._turn_count = 0
        self._doc_sections: dict[str, str] = {}
        self._proposal_outline: list[str] = []

        if session_id:
            # Retomar sessão existente — carrega tudo do Postgres.
            self.session_id = session_id
            self.edital_id = self._load_from_db(session_id, workspace_id)
            self.created_at = self._loaded_created_at
        else:
            if not edital_id:
                raise ValueError("É necessário fornecer session_id (retomar) ou edital_id (criar).")
            self.edital_id = edital_id
            self.session_id = self._create_in_db(workspace_id, edital_id)
            self.created_at = datetime.utcnow().isoformat()

        # Prefixo estático — re-derivado a cada construção (stateless).
        self._profile_context = profile.to_context()
        self._library_context = self._build_library_context(library_items or [])
        self._reflection_insights_context = self._build_reflection_context(workspace_id)

        # Ids dos items anexados explicitamente — guardados pra dedup contra
        # o retrieval automático da biblioteca em turn() (normalizados lower).
        self._library_item_ids: set[str] = set()

        # Decay temporal (ADR B4): atualiza last_referenced_at dos library_items
        # injetados — eles acabaram de ser usados, sobem na fila de relevância.
        if library_items:
            initial_ids = [str(item["id"]) for item in library_items if item.get("id")]
            if initial_ids:
                self._library_item_ids = {i.lower() for i in initial_ids}
                mark_items_referenced(self._db, initial_ids, workspace_id)

        # NOTE (Wave 2 Track A — RAG): documentos do edital NÃO são mais
        # carregados eagerly. A injeção é feita por turno via retrieve_chunks
        # sobre `edital_chunks` (índice gerado por chunk_edital_task). O
        # atributo `_documents_text` é resolvido sob demanda apenas se o
        # fallback de geração de outline precisar dele (caso raro — a maioria
        # dos editais já tem outline persistido em wiki page ou DB).
        self._documents_text_cache: str | None = None

        # Outline: usa o que veio do DB; senão wiki page; senão LLM (1 chamada).
        if not self._proposal_outline:
            self._proposal_outline = (
                self._load_outline_from_wiki(self.edital_id)
                or self._generate_outline()
            )
            self._save_outline()

        # Escopo de RAG: edital primário + análogos (mesmo tema/publico no
        # grafo). Computado uma vez por sessão — o vault muda raramente e a
        # WritingSession é stateless entre requests. Falha cai para
        # [edital_id] silenciosamente (KG indisponível ≠ sessão quebrada).
        self._scope_edital_ids: list[str] = self._resolve_edital_scope()

        logger.info(
            "WritingSession %s | edital=%s | %d seções | turnos=%d | %s/%s",
            self.session_id, self.edital_id, len(self._proposal_outline),
            self._turn_count, self.backend, self.model,
        )

    def _resolve_edital_scope(self) -> list[str]:
        """edital primário + análogos via KGMatchService.resolve_scope.

        Lazy import porque KGMatchService carrega o índice JSON; não vale
        puxar no boot do módulo. Cap em 3 análogos para não inflar o
        retrieval (chunks dos análogos rotulados via format_chunks_for_prompt).
        """
        try:
            from core.kg_match_service import KGMatchService
            return KGMatchService().resolve_scope(
                edital_id=self.edital_id, max_analogues=3,
            )
        except Exception as e:
            logger.warning(
                "[%s] resolve_scope falhou (edital=%s): %s — sessão segue só com primário",
                self.session_id, self.edital_id, e,
            )
            return [self.edital_id]

    # ------------------------------------------------------------------
    # Acesso lazy ao texto completo dos PDFs (fallback)
    # ------------------------------------------------------------------

    @property
    def _documents_text(self) -> str:
        """Carrega PDFs do edital sob demanda. Usado apenas quando
        `_generate_outline` precisa do texto integral para criar o outline da
        proposta — fluxo raro, pois a maioria dos editais já tem outline em
        wiki page. NÃO use em `_build_messages` (substituído por RAG)."""
        if self._documents_text_cache is None:
            self._documents_text_cache = self._load_documents(self.edital_id)
        return self._documents_text_cache

    # ------------------------------------------------------------------
    # Persistência — header
    # ------------------------------------------------------------------

    def _create_in_db(self, workspace_id: str, edital_id: str) -> str:
        """INSERT em writing_sessions; retorna o id gerado.

        Side effect (Fase 3 #23): se houver application_log existente para
        (workspace_id, edital_id) — tipicamente criada pelo OpportunityBrief
        com status='brief_gerado' — linka session_id e avança status para
        'proposta_iniciada'. O trigger log_application_event registra a
        transição em application_events.
        """
        result = self._db.table("writing_sessions").insert({
            "workspace_id": workspace_id,
            "edital_id": edital_id,
            "status": "active",
            "summary": None,
            "proposal_outline": [],
            "section_drafts": {},
        }).execute()
        if not result.data:
            raise RuntimeError("Falha ao criar writing_session no Postgres")
        session_id = result.data[0]["id"]
        self._link_application_log(workspace_id, edital_id, session_id)
        return session_id

    def _link_application_log(
        self, workspace_id: str, edital_id: str, session_id: str,
    ) -> None:
        """Linka writing_session a uma application_log existente, se houver.

        Atualiza session_id (FK) e avança status para 'proposta_iniciada' apenas
        se a application estiver em estado anterior à escrita ('matched' ou
        'brief_gerado'). Caso contrário, NÃO altera o status — usuário pode
        estar retomando trabalho em proposta já submetida/aprovada.
        Falha graciosa: log + continue se DB error.
        """
        try:
            existing = (
                self._db.table("application_log")
                .select("id, status")
                .eq("workspace_id", workspace_id)
                .eq("edital_id", edital_id)
                .maybe_single()
                .execute()
            )
            row = existing.data if existing else None
            if not row:
                return  # Sem application_log prévia — usuário pulou o brief

            update: dict = {"session_id": session_id}
            if row.get("status") in ("matched", "brief_gerado"):
                update["status"] = "proposta_iniciada"

            (
                self._db.table("application_log")
                .update(update)
                .eq("id", row["id"])
                .execute()
            )
            logger.info(
                "writing_session=%s linkada a application_log=%s (novo status=%s)",
                session_id, row["id"], update.get("status", row.get("status")),
            )
        except Exception as e:
            logger.warning(
                "Falha ao linkar writing_session=%s ↔ application_log: %s", session_id, e,
            )

    def _load_from_db(self, session_id: str, workspace_id: str) -> str:
        """SELECT writing_sessions + session_turns; popula estado interno.

        Retorna edital_id. Raises ValueError se sessão inexistente ou de outro
        workspace (RLS já protege, mas validamos explicitamente também).
        """
        result = (
            self._db.table("writing_sessions")
            .select("*")
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        row = result.data if result else None
        if not row:
            raise ValueError(f"Sessão '{session_id}' não encontrada")
        if row["workspace_id"] != workspace_id:
            raise ValueError(f"Sessão '{session_id}' não pertence ao workspace atual")

        self._history_summary = row.get("summary") or ""
        outline = row.get("proposal_outline") or []
        self._proposal_outline = [str(s) for s in outline] if outline else []
        drafts = row.get("section_drafts") or {}
        self._doc_sections = dict(drafts) if isinstance(drafts, dict) else {}
        self._loaded_created_at = row.get("created_at") or datetime.utcnow().isoformat()

        # Carrega turnos (apenas user/assistant; system seria contexto de prefixo).
        turns_result = (
            self._db.table("session_turns")
            .select("turn_index, role, content")
            .eq("session_id", session_id)
            .order("turn_index", desc=False)
            .execute()
        )
        turns = turns_result.data or []
        self._history = [
            {"role": t["role"], "content": t["content"]}
            for t in turns
            if t["role"] in ("user", "assistant")
        ]
        # turn_count = número de turnos completos do usuário.
        max_idx = max((t["turn_index"] for t in turns), default=0)
        self._turn_count = max_idx

        return row["edital_id"]

    def _save_outline(self) -> None:
        try:
            self._db.table("writing_sessions").update({
                "proposal_outline": self._proposal_outline,
            }).eq("id", self.session_id).execute()
        except Exception as e:
            logger.warning("[%s] Falha ao salvar outline: %s", self.session_id, e)

    # ------------------------------------------------------------------
    # Carregamento dos documentos
    # ------------------------------------------------------------------

    def _load_documents(self, edital_id: str) -> str:
        """Carrega todos os PDFs relevantes do edital e retorna texto concatenado.

        Aplica o mesmo filtro de versões usado no chunker (tasks.py) pra evitar
        concatenar 4 versões do FAQ + 4 rerratificações no contexto do outline.
        Import lazy de `core.tasks._filter_to_latest_versions` pra não puxar
        `procrastinate` no boot do módulo.
        """
        # Import lazy — procrastinate é pesado e não é precisão pro caso comum.
        from core.tasks import _filter_to_latest_versions

        pdf_dir = FINEP_PDFS_DIR / edital_id
        if not pdf_dir.exists():
            logger.warning("Diretório de PDFs não encontrado: %s", pdf_dir)
            return ""

        candidates = [p for p in sorted(pdf_dir.glob("*.pdf"))
                      if not any(kw in p.stem.lower() for kw in _SKIP_KEYWORDS)]
        candidates = _filter_to_latest_versions(candidates)

        parts = []
        for pdf_path in candidates:
            text = self._extract_pdf(pdf_path)
            if text.strip():
                parts.append(f"### {pdf_path.stem}\n{text}")

        result = "\n\n".join(parts)
        logger.info("Documentos carregados: %d chars de %s (%d PDFs)",
                    len(result), edital_id, len(candidates))
        return result

    @staticmethod
    def _extract_pdf(pdf_path: Path) -> str:
        try:
            import pdfplumber
            pages = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    pages.append(page.extract_text() or "")
            return "\n".join(pages)
        except Exception as e:
            logger.warning("Erro ao extrair %s: %s", pdf_path.name, e)
            return ""

    # ------------------------------------------------------------------
    # Outline da proposta
    # ------------------------------------------------------------------

    @staticmethod
    def _load_outline_from_wiki(edital_id: str) -> list[str]:
        """Lê proposal_sections da wiki page — zero custo de LLM."""
        wiki_file = KG_WIKI_DIR / f"{edital_id}.json"
        if not wiki_file.exists():
            return []
        try:
            wiki_page = json.loads(wiki_file.read_text(encoding="utf-8"))
            sections = wiki_page.get("proposal_sections", [])
            return [str(s) for s in sections] if sections else []
        except Exception:
            return []

    def _generate_outline(self) -> list[str]:
        """Gera o outline das seções da proposta via LLM (1 chamada por sessão)."""
        if not self._documents_text:
            return self._default_outline()

        context = self._documents_text[:12000]  # resumo para geração do outline
        messages = [
            {"role": "system", "content": OUTLINE_SYSTEM},
            {"role": "user",   "content": f"DOCUMENTOS DO EDITAL:\n{context}"},
        ]
        success, text, _ = self._call_llm(messages, temperature=0.1, max_tokens=500)

        if success:
            try:
                outline = json.loads(text)
                if isinstance(outline, list) and outline:
                    return [str(s) for s in outline]
            except json.JSONDecodeError:
                match = re.search(r"\[.*?\]", text, re.DOTALL)
                if match:
                    try:
                        outline = json.loads(match.group(0))
                        if isinstance(outline, list):
                            return [str(s) for s in outline]
                    except json.JSONDecodeError:
                        pass

        return self._default_outline()

    @staticmethod
    def _default_outline() -> list[str]:
        return [
            "1. Identificação da empresa",
            "2. Objeto do projeto",
            "3. Justificativa e relevância",
            "4. Objetivos",
            "5. Metodologia e plano de trabalho",
            "6. Equipe técnica",
            "7. Cronograma",
            "8. Orçamento",
        ]

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def get_info(self) -> dict:
        # `has_documents` checa apenas o diretório no disco — NÃO carrega os
        # PDFs (que agora são consumidos via RAG). Mantemos o campo para
        # compatibilidade com clientes que já o leem.
        pdf_dir = FINEP_PDFS_DIR / self.edital_id
        has_documents = pdf_dir.exists() and any(pdf_dir.glob("*.pdf"))
        return {
            "session_id":       self.session_id,
            "edital_id":        self.edital_id,
            "section_titles":   self._proposal_outline,
            "has_documents":    has_documents,
            "turn_count":       self._turn_count,
            "created_at":       self.created_at,
        }

    def turn(self, user_message: str, section_hint: str | None = None) -> dict:
        self._turn_count += 1
        user_turn_index = self._turn_count
        logger.info("[%s] Turno %d", self.session_id, self._turn_count)

        try:
            if self._turn_count > COMPRESS_THRESHOLD:
                self._compress_history()

            # @ mentions: resolve referências a library_items no input do usuário.
            # Items inválidos/inacessíveis (RLS) são silenciosamente ignorados.
            mentions_context = self._resolve_mentions(user_message)

            # Embeda a mensagem UMA vez — reusada pelo RAG do edital e pelo
            # retrieval da biblioteca (evita 2 chamadas OpenAI/turno). Se o
            # embed falhar (OpenAI down), pulamos ambos os retrievals: opera
            # só com perfil + histórico, mesmo comportamento de antes.
            query_vec: list[float] | None = None
            try:
                query_vec = embed_query(user_message)
            except Exception as e:
                logger.warning(
                    "[%s] embed_query falhou: %s — turno sem RAG.",
                    self.session_id, e,
                )

            # RAG: busca trechos relevantes ANTES de montar o prompt. Falhas
            # de retrieval (DB offline, edital ainda não indexado, etc.) NÃO
            # devem fazer fallback para context-stuffing — o ponto da nova
            # arquitetura é justamente não despejar PDFs inteiros no prompt.
            # Em caso de falha, operamos só com perfil + histórico e logamos
            # alto para que o operador possa rodar `scripts/reindex_edital.py`.
            edital_chunks_context = ""
            if query_vec is not None:
                try:
                    chunks = retrieve_chunks(
                        self._db, self._scope_edital_ids, query=user_message, k=5,
                        query_vec=query_vec,
                    )
                    if chunks:
                        edital_chunks_context = format_chunks_for_prompt(
                            chunks, edital_ids=self._scope_edital_ids,
                        )
                    else:
                        logger.warning(
                            "[%s] Nenhum chunk retornado para edital=%s — "
                            "edital pode não estar indexado ainda.",
                            self.session_id, self.edital_id,
                        )
                except Exception as e:
                    logger.warning(
                        "[%s] Falha em retrieve_chunks (edital=%s): %s — "
                        "seguindo sem contexto de edital.",
                        self.session_id, self.edital_id, e,
                    )

            # Retrieval automático da biblioteca (Fase 2 #16). Dedup contra
            # anexos explícitos + @-mentions do turno pra não duplicar docs.
            retrieved_library_context = ""
            if query_vec is not None:
                exclude_ids = self._library_item_ids | {
                    m.lower() for m in _MENTION_RE.findall(user_message)
                }
                retrieved_library_context = self._build_retrieved_library_context(
                    user_message, query_vec, exclude_ids,
                )

            messages = self._build_messages(
                user_message, section_hint, edital_chunks_context,
                mentions_context, retrieved_library_context,
            )
            success, response_text, error_type = self._call_llm(messages)

            if not success:
                # Rollback do contador para que o próximo turno reaproveite o índice.
                self._turn_count -= 1
                return self._error_result(response_text, error_type)

            # Grantable: separa o rascunho (auto-salvo) do texto de chat.
            clean_text, draft_content = _extract_draft(response_text)

            self._history.append({"role": "user",      "content": user_message})
            self._history.append({"role": "assistant", "content": clean_text})

            # Persistência dos dois turnos (best-effort). O histórico guarda o
            # texto sem a tag — o rascunho vive em section_drafts, não no chat.
            self._persist_turn(user_turn_index, "user", user_message, section_hint)
            self._persist_turn(user_turn_index, "assistant", clean_text, section_hint)

            # Auto-save: LLM escreve direto no documento quando há seção ativa.
            # Sobrescreve sem histórico (decisão de design — versionamento flat).
            if draft_content and section_hint:
                self.set_section_content(section_hint, draft_content)

            return {
                "session_id":        self.session_id,
                "assistant_message": clean_text,
                "draft_content":     draft_content,
                "turn_number":       self._turn_count,
                "success":           True,
                "error":             None,
            }

        except Exception as e:
            logger.error("[%s] Erro no turno %d: %s", self.session_id, self._turn_count, e)
            return self._error_result(str(e), "INTERNAL_ERROR")

    def _persist_turn(
        self,
        turn_index: int,
        role: str,
        content: str,
        section_hint: str | None,
    ) -> None:
        """INSERT em session_turns. Falhas são logadas mas não interrompem o turno.

        O par (user, assistant) compartilha o turn_index — distinguidos por role.
        Como a constraint UNIQUE é (session_id, turn_index), usamos índices
        ímpares e pares para evitar colisão: user=2k-1, assistant=2k.
        """
        # Re-mapeia para garantir unicidade no DB:
        # turn_index lógico N → user em 2N-1, assistant em 2N.
        if role == "user":
            db_index = turn_index * 2 - 1
        elif role == "assistant":
            db_index = turn_index * 2
        else:
            db_index = turn_index

        try:
            self._db.table("session_turns").insert({
                "session_id": self.session_id,
                "turn_index": db_index,
                "role": role,
                "content": content,
                "section_hint": section_hint,
            }).execute()
        except Exception as e:
            logger.warning(
                "[%s] Falha ao persistir turno %d (%s): %s",
                self.session_id, db_index, role, e,
            )

    def get_section_starter(self, section_title: str) -> str:
        """Mensagem inicial contextualizada para uma seção da proposta.

        Usa RAG (busca o título da seção como query) em vez de injetar o PDF
        inteiro. Se a busca falhar, opera só com perfil — não regride para
        context-stuffing.
        """
        messages = [
            {"role": "system", "content": WRITER_SYSTEM},
            {"role": "user",   "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
        ]

        try:
            chunks = retrieve_chunks(
                self._db, self._scope_edital_ids, query=section_title, k=3,
            )
            chunks_text = (
                format_chunks_for_prompt(chunks, edital_ids=self._scope_edital_ids)
                if chunks else ""
            )
        except Exception as e:
            logger.warning(
                "[%s] get_section_starter: retrieve_chunks falhou: %s",
                self.session_id, e,
            )
            chunks_text = ""

        if chunks_text:
            messages.append({"role": "user", "content": chunks_text})

        messages.append({
            "role": "user",
            "content": (
                f"Gere uma mensagem de boas-vindas curta (máx. 3 frases) para a seção "
                f"'{section_title}'. Mencione o que deve conter e como o perfil da empresa "
                f"se conecta ao edital. Termine com uma sugestão de por onde começar."
            ),
        })
        success, text, _ = self._call_llm(messages, temperature=0.4, max_tokens=300)
        return text if success else f"Vamos trabalhar na seção **{section_title}**. Como posso ajudar?"

    # ------------------------------------------------------------------
    # Document state
    # ------------------------------------------------------------------

    def get_document(self) -> dict:
        return {
            "session_id": self.session_id,
            "sections": [
                {"title": t, "content": self._doc_sections.get(t, "")}
                for t in self._proposal_outline
            ],
        }

    def set_section_content(self, section_title: str, content: str) -> None:
        self._doc_sections[section_title] = content
        try:
            # JSONB merge: read-modify-write. supabase-py não tem operador jsonb_set,
            # mas o objeto é pequeno (poucas seções) — escrever inteiro é seguro.
            self._db.table("writing_sessions").update({
                "section_drafts": self._doc_sections,
            }).eq("id", self.session_id).execute()
        except Exception as e:
            logger.warning(
                "[%s] Falha ao persistir section_drafts (%s): %s",
                self.session_id, section_title, e,
            )

    def get_export(self) -> str:
        parts = []
        for title in self._proposal_outline:
            content = self._doc_sections.get(title, "")
            parts.append(f"## {title}\n\n{content}" if content else f"## {title}\n\n*[A preencher]*")
        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------
    # Montagem do prompt (prefixo estático → prompt caching)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # @ mentions resolver (Wave 2 Track A #8)
    # ------------------------------------------------------------------

    def _resolve_mentions(self, user_message: str) -> str:
        """Resolve tokens @<uuid> no input do usuário a contexto adicional.

        Para cada UUID encontrado:
          - Fetch via get_item (RLS — items de outros workspaces retornam None)
          - Marca o item como referenciado (last_referenced_at = now)
          - Adiciona um bloco com title/type/summary/key_facts no contexto

        Items duplicados na mesma mensagem são resolvidos uma única vez.
        Items inválidos/inacessíveis são silenciosamente ignorados.

        Returns: string formatada para injeção no prompt, ou "" se nenhuma
        mention foi encontrada/resolvida.
        """
        ids = list(dict.fromkeys(_MENTION_RE.findall(user_message)))
        if not ids:
            return ""

        resolved: list[dict] = []
        for item_id in ids:
            try:
                item = get_item(self._db, item_id, self.workspace_id)
            except Exception as e:
                logger.warning(
                    "[%s] @mention resolve falhou (id=%s): %s",
                    self.session_id, item_id, e,
                )
                continue
            if item:
                resolved.append(item)

        if not resolved:
            return ""

        # Atualiza last_referenced_at para os items efetivamente resolvidos
        mark_items_referenced(
            self._db, [str(item["id"]) for item in resolved], self.workspace_id,
        )

        # Formata bloco de contexto para o LLM
        parts = ["ITENS REFERENCIADOS PELO USUÁRIO VIA @ MENTION:"]
        for item in resolved:
            title = item.get("title", "(sem título)")
            type_ = item.get("type", "doc")
            summary = item.get("summary", "")
            key_facts = item.get("key_facts", []) or []
            parts.append(f"\n[{type_.upper()}] {title}")
            if summary:
                parts.append(f"  {summary}")
            for fact in key_facts[:8]:
                parts.append(f"  • {fact}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Montagem do prompt (prefixo estático → prompt caching)
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        user_message: str,
        section_hint: str | None = None,
        edital_chunks_context: str = "",
        mentions_context: str = "",
        retrieved_library_context: str = "",
    ) -> list[dict]:
        """
        Estrutura do prompt (estático primeiro para maximizar cache hit):
          system   — WRITER_SYSTEM (imutável)
          user     — perfil da empresa (imutável na sessão)
          user     — contexto da biblioteca anexado (imutável na sessão)
          user     — histórico comprimido (estável dentro de uma janela)
          ...      — histórico recente (turnos verbatim)
          user     — TRECHOS DO EDITAL retornados pelo RAG (varia por turno!
                     fica depois do prefixo estável p/ preservar cache hit)
          user     — items @-mencionados no turno (varia por turno)
          user     — items da biblioteca recuperados automaticamente (varia)
          user     — seção ativa (se houver)
          user     — mensagem atual

        Note: tudo que varia por turno (RAG, @-mentions, retrieval auto da
        biblioteca) DEVE ficar depois do prefixo cacheable para não invalidar
        o cache de prompt. Ordem entre os blocos por-turno: edital (mais
        autoritativo p/ compliance) → @-mention (sinal humano) → biblioteca
        auto (especulativo).
        """
        messages: list[dict] = [
            {"role": "system", "content": WRITER_SYSTEM},
            {"role": "user",   "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
        ]

        if self._library_context:
            messages.append({
                "role":    "user",
                "content": self._library_context,
            })

        if self._reflection_insights_context:
            messages.append({
                "role":    "user",
                "content": self._reflection_insights_context,
            })

        if self._history_summary:
            messages.append({
                "role":    "user",
                "content": self._history_summary,
            })

        messages.extend(self._history)

        # Trechos do edital (varia por turno) — depois do prefixo estável.
        if edital_chunks_context:
            messages.append({
                "role":    "user",
                "content": edital_chunks_context,
            })

        # Items referenciados via @ mention no turno atual.
        if mentions_context:
            messages.append({
                "role":    "user",
                "content": mentions_context,
            })

        # Items da biblioteca recuperados automaticamente por relevância.
        if retrieved_library_context:
            messages.append({
                "role":    "user",
                "content": retrieved_library_context,
            })

        if section_hint:
            messages.append({"role": "user", "content": f"[Seção ativa: {section_hint}]"})

        messages.append({"role": "user", "content": user_message})
        return messages

    # ------------------------------------------------------------------
    # Compressão de histórico
    # ------------------------------------------------------------------

    def _compress_history(self) -> None:
        if len(self._history) <= HISTORY_WINDOW * 2:
            return

        to_compress = self._history[:-(HISTORY_WINDOW * 2)]
        self._history = self._history[-(HISTORY_WINDOW * 2):]

        turns_text = "\n".join(
            f"{msg['role'].upper()}: {msg['content']}" for msg in to_compress
        )
        messages = [
            {"role": "system", "content": COMPRESS_SYSTEM},
            {"role": "user",   "content": f"Turnos anteriores:\n\n{turns_text}"},
        ]
        success, summary, _ = self._call_llm(messages, temperature=0.3, max_tokens=300)
        if success and summary.strip():
            self._history_summary = f"[Resumo anterior: {summary.strip()}]\n\n" + self._history_summary
            logger.info("[%s] Histórico comprimido", self.session_id)
            # Persiste o novo summary.
            try:
                self._db.table("writing_sessions").update({
                    "summary": self._history_summary,
                }).eq("id", self.session_id).execute()
            except Exception as e:
                logger.warning("[%s] Falha ao salvar summary: %s", self.session_id, e)

    # ------------------------------------------------------------------
    # Content Library
    # ------------------------------------------------------------------

    @staticmethod
    def _build_library_context(items: list[dict]) -> str:
        if not items:
            return ""
        parts = ["NARRATIVAS DA EMPRESA (propostas e projetos anteriores):"]
        for item in items:
            parts.append(f"\n[{item.get('type', 'doc').upper()}] {item.get('title', '')}")
            for fact in item.get("key_facts", [])[:10]:
                parts.append(f"  • {fact}")
        return "\n".join(parts)

    def _build_retrieved_library_context(
        self,
        query: str,
        query_vec: list[float],
        exclude_ids: set[str],
    ) -> str:
        """Retrieval automático de items relevantes da biblioteca (Fase 2 #16).

        Diferente do anexo explícito (`_build_library_context`) e do @-mention
        (`_resolve_mentions`), aqui o retrieval é por relevância, sem ação do
        usuário. Scoring α·recency + β·importance·decay + γ·relevance vem de
        `retrieve_library_items`.

        Decisões deliberadas:
          - NÃO chama `mark_items_referenced`: auto-recuperação não pode
            esquentar o decay temporal, senão cria loop de auto-reforço
            (recuperado → last_referenced_at fresco → importance_decay sobe →
            recuperado de novo). Só sinal humano (anexo/@-mention) alimenta o
            decay.
          - Dedup contra `exclude_ids` (anexo explícito + @-mentions do turno)
            pra não injetar o mesmo doc 2–3×.
          - Filtra por `relevance_score` (cosine) >= LIBRARY_RELEVANCE_MIN —
            `retrieve_library_items` sempre devolve k items mesmo sem match
            real; o piso evita despejar ruído.

        Falha graciosa: retorna "" (turno segue sem o bloco).
        """
        # Pede k extra pra dedup não deixar o resultado abaixo de K.
        want = min(LIBRARY_RETRIEVAL_K + len(exclude_ids), 15)
        try:
            items = retrieve_library_items(
                self.workspace_id, query, k=want, query_vec=query_vec,
            )
        except Exception as e:
            logger.warning(
                "[%s] retrieve_library_items falhou: %s", self.session_id, e,
            )
            return ""

        picked: list[dict] = []
        for it in items:
            if str(it.get("id", "")).lower() in exclude_ids:
                continue
            if (it.get("relevance_score") or 0.0) < LIBRARY_RELEVANCE_MIN:
                continue
            picked.append(it)
            if len(picked) >= LIBRARY_RETRIEVAL_K:
                break

        if not picked:
            return ""

        parts = [
            "POSSÍVEL CONTEXTO DA BIBLIOTECA (recuperado automaticamente — "
            "use o que se aplicar à seção atual; ignore o que não couber):",
        ]
        for it in picked:
            type_ = (it.get("type") or "doc").upper()
            parts.append(f"\n[{type_}] {it.get('title', '(sem título)')}")
            if it.get("summary"):
                parts.append(f"  {it['summary']}")
            for fact in (it.get("key_facts") or [])[:4]:
                parts.append(f"  • {fact}")
        return "\n".join(parts)

    def _build_reflection_context(self, workspace_id: str) -> str:
        """Carrega insights ativos do ReflectionService (Fase 2 #18).

        Os insights são síntese de outcomes de aplicações anteriores deste
        workspace, gerados periodicamente pelo `reflect_workspace_task`. Falhas
        de leitura (tabela vazia, RLS, DB offline) viram fallback silencioso —
        a WritingSession opera sem insights e loga em debug.
        """
        try:
            insights = load_active_insights(self._db, workspace_id, max_total=6)
        except Exception as e:
            logger.debug("Falha ao carregar reflection_insights: %s", e)
            return ""
        if not insights:
            return ""
        parts = [
            "INSIGHTS DA EMPRESA (síntese de aplicações anteriores — use como pano de fundo, "
            "não cite explicitamente):",
        ]
        for ins in insights:
            level_label = "Padrão" if ins.get("level") == 2 else "Observação"
            parts.append(f"• [{level_label}] {ins.get('insight', '')}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Chamadas LLM
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        messages: list[dict],
        temperature: float = 0.5,
        max_tokens: int = 2000,
    ) -> tuple[bool, str, str | None]:
        if self.backend == "ollama":
            return self._call_ollama(messages, temperature, max_tokens)
        return self._call_openai(messages, temperature, max_tokens)

    def _call_ollama(self, messages, temperature, max_tokens):
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model":    self.model,
                    "messages": messages,
                    "stream":   False,
                    "options":  {"temperature": temperature, "num_predict": max_tokens},
                },
                timeout=300,
            )
            if response.status_code != 200:
                return False, f"Ollama retornou {response.status_code}", "API_ERROR"
            return True, response.json()["message"]["content"], None
        except requests.exceptions.Timeout:
            return False, "Timeout na chamada Ollama", "TIMEOUT"
        except requests.exceptions.ConnectionError:
            return False, "Ollama não acessível", "CONNECTION_ERROR"
        except Exception as e:
            return False, str(e), "UNKNOWN_ERROR"

    def _call_openai(self, messages, temperature, max_tokens):
        if not OPENAI_API_KEY:
            return False, "OPENAI_API_KEY não configurada", "CONFIG_ERROR"
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return True, response.choices[0].message.content, None
        except ImportError:
            return False, "Biblioteca openai não instalada", "DEPENDENCY_ERROR"
        except Exception as e:
            return False, str(e), "API_ERROR"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _error_result(self, message: str, error_type: str | None) -> dict:
        return {
            "session_id":        self.session_id,
            "assistant_message": f"Erro ao processar: {message}",
            "turn_number":       self._turn_count,
            "success":           False,
            "error":             message,
            "error_type":        error_type,
        }


# =============================================================================
# Module-level helpers
# =============================================================================

def list_sessions(
    db: Client,
    workspace_id: str,
    status: str | None = None,
) -> list[dict]:
    """Lista sessões do workspace, ordenadas por updated_at desc.

    Retorna registros leves: id, edital_id, status, created_at, updated_at,
    turn_count. O turn_count é derivado de um SELECT count agregado em
    session_turns por sessão.
    """
    query = (
        db.table("writing_sessions")
        .select("id, edital_id, status, created_at, updated_at")
        .eq("workspace_id", workspace_id)
        .order("updated_at", desc=True)
    )
    if status:
        query = query.eq("status", status)
    result = query.execute()
    sessions = result.data or []

    if not sessions:
        return []

    # Contagem leve de turnos por sessão. supabase-py expõe head=True + count
    # para evitar trafegar as rows, mas para múltiplas sessões fazemos uma única
    # consulta agrupada client-side (uma row por turno é suficiente porque
    # turn_index é apenas um inteiro).
    session_ids = [s["id"] for s in sessions]
    try:
        turns_result = (
            db.table("session_turns")
            .select("session_id, turn_index")
            .in_("session_id", session_ids)
            .execute()
        )
        counts: dict[str, int] = {}
        for row in turns_result.data or []:
            counts[row["session_id"]] = counts.get(row["session_id"], 0) + 1
        for s in sessions:
            # Dois rows de session_turns por turno lógico (user + assistant).
            s["turn_count"] = counts.get(s["id"], 0) // 2
    except Exception as e:
        logger.warning("Falha ao contar turnos para list_sessions: %s", e)
        for s in sessions:
            s["turn_count"] = 0

    for s in sessions:
        s["session_id"] = s.pop("id")

    return sessions


def delete_session(db: Client, session_id: str, workspace_id: str) -> bool:
    """Apaga a sessão e seus turnos. Retorna False se não pertencer ao workspace."""
    check = (
        db.table("writing_sessions")
        .select("id")
        .eq("id", session_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    if not check.data:
        return False
    db.table("session_turns").delete().eq("session_id", session_id).execute()
    db.table("writing_sessions").delete().eq("id", session_id).execute()
    return True


def get_session_document(
    db: Client,
    session_id: str,
    workspace_id: str,
) -> dict | None:
    """Lê writing_sessions.section_drafts + proposal_outline (sem reconstruir
    o objeto WritingSession). Útil para o endpoint leve
    GET /writing/sessions/{id}/document.
    """
    result = (
        db.table("writing_sessions")
        .select("id, edital_id, proposal_outline, section_drafts, workspace_id")
        .eq("id", session_id)
        .maybe_single()
        .execute()
    )
    row = result.data if result else None
    if not row:
        return None
    if row["workspace_id"] != workspace_id:
        return None
    outline = row.get("proposal_outline") or []
    drafts = row.get("section_drafts") or {}
    return {
        "session_id": row["id"],
        "edital_id": row["edital_id"],
        "sections": [
            {"title": t, "content": drafts.get(t, "")}
            for t in outline
        ],
    }
