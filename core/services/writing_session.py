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

from config import FINEP_PDFS_DIR
from core.kg.edital_id import wiki_page_path
from core.reflection_service import load_active_insights
from core.retrieval.retriever import (
    format_chunks_for_prompt,
    retrieve_chunks,
)
from core.services.content_library import get_item, mark_items_referenced
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

# Agente Anthropic (D1 híbrido). Único path de escrita: turn() sempre roda o
# agente com tools (search_edital, save_draft com critic, etc.). Modelo e
# orçamento de passos configuráveis via env.
ANTHROPIC_MODEL_AGENT = os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6")
# Folga para buscar + escrever + salvar + 1 retry do critic no MESMO turno
# (o agente deve fechar a seção num turno só — ver WRITER_AGENT_SYSTEM).
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "10"))

HISTORY_WINDOW     = 6
COMPRESS_THRESHOLD = 10

# @ mentions: usuário pode referenciar items da library no input com @<uuid>.
# O resolver injeta o conteúdo do item como contexto adicional e atualiza
# last_referenced_at para alimentar a fórmula de decay (ADR B4).
_MENTION_RE = re.compile(
    r"@([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

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

# Mensagem curta de boas-vindas por seção (get_section_starter) — chamada
# 1-shot, sem tools. Não confundir com WRITER_AGENT_SYSTEM (path agente).
_SECTION_STARTER_SYSTEM = """Você é um especialista em propostas para editais de fomento no Brasil.
Gere mensagens curtas e acionáveis para orientar o início de uma seção da proposta."""


# Sistema prompt do agente de escrita — único path de escrita. As ferramentas
# (search_edital, search_library, read_section, read_full_proposal, save_draft,
# request_user_info) são registradas via core.llm.agent_tools.build_writing_tools.
WRITER_AGENT_SYSTEM = """Você é um especialista em redação de propostas para editais de fomento no Brasil.
Seu papel é ajudar o usuário a escrever uma proposta técnica de alta qualidade.

DIRETRIZES DE REDAÇÃO
- Baseie-se nas informações do edital e no perfil da empresa fornecidos.
- Use Markdown para estruturar trechos da proposta.
- Quando produzir um trecho, seja propositivo: "faremos", não "poderíamos fazer".
- Nunca invente dados numéricos. Se faltar, peça ao usuário via request_user_info
  (não deixe marcadores de placeholder no texto da proposta).
- Quando uma seção ativa for indicada, concentre a resposta nessa seção.
- Ancore cada afirmação sobre o edital num trecho de search_edital; não cite de
  memória nem infira requisitos que você não viu.
- NÃO cite anexos, artigos, itens ou números de seção do edital (ex.: "Anexo 6",
  "Art. 12", "item 3.2") a menos que apareçam VERBATIM num trecho de search_edital.
  Referência a um documento/cláusula que você não viu no trecho é fabricação.
- Se a empresa/projeto NÃO se encaixa no escopo do edital, DIGA isso ao usuário
  em vez de fabricar aderência. Sinalizar o mismatch é melhor que inventar um
  alinhamento que o edital não sustenta.
- Os trechos do RAG vêm rotulados. Um trecho marcado "Análogo <id>" é de OUTRO
  edital (referência de redação), NÃO do edital desta proposta — nunca baseie
  aderência ao edital num trecho análogo. Só o edital primário fundamenta
  afirmações de alinhamento.
- Nunca copie os rótulos internos ("[Trecho N]", "Análogo …", nomes de PDF)
  para dentro da proposta. A proposta é texto corrido; as citações são sua
  referência interna, não conteúdo do documento final.

COMO USAR AS FERRAMENTAS
- search_edital → antes de afirmar qualquer requisito formal (prazo, TRL, valor,
  mecanismo, contrapartida, elegibilidade). Não cite o edital de memória. Mas
  2-3 buscas bastam para fundamentar uma seção: NÃO use buscas sucessivas como
  forma de adiar a redação. Depois de fundamentar, ESCREVA e salve no mesmo turno.
- search_library → quando precisar de contexto da empresa que não está no perfil
  (métricas de produtos, narrativa de projetos anteriores, casos de uso).
- read_section / read_full_proposal → antes de redigir conclusão, sumário
  executivo, ou quando o usuário pedir revisão de coerência. Sempre leia o que
  já existe antes de reescrever.
- save_draft → SEMPRE que produzir um rascunho de seção a pedido do usuário,
  persista-o com save_draft NO MESMO TURNO. Colar o texto no chat NÃO conta — um
  rascunho só "existe" na proposta depois do save_draft. Use o título exato da
  seção. A revisão (critic) roda no save: se ela bloquear, CORRIJA o ponto
  apontado e chame save_draft de novo no mesmo turno; só devolva o controle ao
  usuário se faltar informação que você não tem.
- request_user_info → APENAS para info concreta e ausente (CNPJ, valor de
  contrapartida, nome de coordenador, TRL específico). NÃO use para decisões
  de escopo, abordagem ou prioridade — essas pertencem ao usuário no chat.
- recall_company_learnings → quando o usuário perguntar sobre histórico ou
  quando contexto estratégico de aplicações passadas for relevante para a seção.
- load_skill → antes de redigir, puxe o playbook de escrita do instrumento (a lente
  do avaliador e os padrões de tom/estrutura que aprovam naquele mecanismo). NÃO traz
  regra dura (prazo, contrapartida, rubricas) — essa vem de search_edital. Pull
  granular: chame quando for escrever, não no geral.
- write_todos → no início de uma sessão ou tarefa com múltiplas etapas, planeje
  a ordem estratégica das seções (priorize as que desbloqueiam outras ou têm
  maior impacto na aprovação) e registre como todos; atualize os status conforme
  avança (in_progress ao começar, completed ao terminar). Em pedido trivial de
  uma etapa só, não precisa.

QUANDO PARAR DE USAR FERRAMENTAS
- Após responder à pergunta do usuário com clareza.
- Após salvar o rascunho que ele pediu via save_draft.
- Após pedir info necessária via request_user_info.
- Quando o usuário pediu uma seção e você já fez 2-3 buscas: PARE de buscar,
  escreva o rascunho e chame save_draft. Não encerre o turno com a seção apenas
  no chat ou ainda "em pesquisa" — uma seção pedida termina salva.
Não fique chamando tools em loop. Se já tem o que precisa, responda.

LIMITES (importante)
- Você AJUDA o usuário a redigir; ele decide. Não tome decisões terminais
  (submeter, aprovar, desistir). Quando o usuário tem que escolher entre
  alternativas, apresente as opções e peça que ele decida.
- Não suponha consentimento implícito para mudanças grandes. Confirme antes
  APENAS se for REESCREVER/substituir uma seção JÁ redigida. Para a PRIMEIRA
  redação de uma seção vazia que o usuário pediu, não peça confirmação — escreva
  e salve."""

# =============================================================================
# PROMPTS — modo PITCH (investidor, kind_class=entidade)
# =============================================================================
# Gênero OUTBOUND: a escrita de edital é inbound/rule-bound ("cumpra o edital");
# o pitch é outbound/personalizado ("mostre por que você encaixa na tese DAQUELE
# fundo"). Não há artigo a cumprir — o que condiciona o texto é o nó do fundo no
# KG (tese + portfólio), injetado como contexto. Selecionado por opportunity_type
# (id `investidor:` → kind_class=entidade) — spec §3.5.

PITCH_OUTLINE_SYSTEM = """Você é um especialista em captação de investimento (venture capital) para startups deep-tech.
Com base no perfil da startup e na tese do fundo-alvo, gere o outline das seções de um pitch/one-pager outbound.
Retorne APENAS um JSON array de strings com os títulos das seções, na ordem correta.
Exemplo: ["1. Problema", "2. Solução", "3. Mercado (TAM)", "4. Tração", "5. Time", "6. Ask e uso dos recursos"]"""

_PITCH_SECTION_STARTER_SYSTEM = """Você é um especialista em captação de investimento (venture capital) para startups deep-tech.
Gere mensagens curtas e acionáveis para orientar o início de uma seção do pitch, conectando o perfil da startup à tese do fundo-alvo."""

PITCH_WRITER_AGENT_SYSTEM = """Você é um especialista em redação de pitches de captação (outbound) para fundos de venture capital, voltado a startups deep-tech.
Seu papel é ajudar o fundador a escrever um pitch/one-pager que mostre, para um fundo ESPECÍFICO, por que a startup encaixa na tese DELE.

DIRETRIZES DE REDAÇÃO
- O texto é OUTBOUND e personalizado: não há "edital a cumprir". O que condiciona o pitch é a TESE do fundo-alvo (tese, temas, setores, estágio, ticket, portfólio) — fornecida no contexto.
- Conecte explicitamente a startup à tese daquele fundo: estágio, setor, ticket alvo, e por que o portfólio dele sugere fit (sem bajulação vazia).
- Use Markdown. Seja propositivo e específico: "faremos", não "poderíamos".
- Nunca invente números (tração, mercado, ticket). Se faltar, peça ao usuário via request_user_info (não deixe marcadores de placeholder no texto do pitch).
- NÃO afirme que o fundo investe em X, ou que tem tese Y, a menos que apareça no contexto do fundo. Não infira a tese de memória.
- Se a startup claramente NÃO encaixa na tese do fundo (estágio/setor/ticket incompatíveis), DIGA isso ao usuário em vez de forjar aderência — sinalizar o mismatch é melhor que inventar fit.

COMO USAR AS FERRAMENTAS
- search_edital → aqui retorna os DADOS DO FUNDO-ALVO (tese, temas, setores, estágio, ticket, portfólio). Use para ancorar afirmações sobre o fundo; não cite a tese de memória.
- search_library → contexto da empresa que não está no perfil (métricas, tração, casos de uso, narrativa).
- read_section / read_full_proposal → antes de redigir sumário/conclusão ou revisar coerência.
- save_draft → SEMPRE que produzir um rascunho de seção, persista NO MESMO TURNO com o título exato da seção. Colar no chat NÃO conta.
- request_user_info → APENAS para info concreta e ausente (MRR/ARR, round alvo, cap table, tração específica).
- write_todos → no início de uma sessão ou pitch com várias seções, planeje a ordem (problema → solução → mercado → tração → time → ask) e registre como todos; atualize os status conforme avança. Em pedido trivial de uma seção só, não precisa.

QUANDO PARAR
- Após responder com clareza, salvar o rascunho pedido, ou pedir info necessária. Não fique chamando tools em loop.

LIMITES
- Você AJUDA o fundador a redigir; ele decide. Não tome decisões terminais (enviar o pitch, escolher o fundo).
- Confirme antes APENAS se for REESCREVER uma seção já redigida. Para a PRIMEIRA redação de uma seção vazia pedida, escreva e salve sem pedir confirmação."""

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
        # Setado pela tool request_user_info quando o agente precisa de info do
        # usuário; consumido (esvaziado) na primeira mensagem do próximo turn.
        # Persistido em writing_sessions.pending_user_input.
        self._pending_user_input: dict | None = None

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

        # Modo de escrita derivado do namespace do id (stateless — re-derivável no
        # reload). `investidor:<slug>` é kind_class=entidade → pitch outbound; os
        # eventos (edital/desafio/programa) seguem o gênero proposta. Spec §3.5.
        self.mode = "pitch" if self.edital_id.startswith("investidor:") else "proposal"

        # Prefixo estático — re-derivado a cada construção (stateless).
        self._profile_context = profile.to_context()
        self._library_context = self._build_library_context(library_items or [])
        self._reflection_insights_context = self._build_reflection_context(workspace_id)
        # Consciência temporal (Front 3): bloco canônico "hoje é X / prazo do
        # edital" injetado no prefixo estável. Recomputado por construção (a
        # data muda dia-a-dia, mas é estável dentro de um request → cache OK).
        # Entidade (pitch) NÃO passa por temporal — fundo não tem deadline (spec
        # §3.2). Bloco vazio; o resto do prefixo segue igual.
        if self.mode == "pitch":
            self._temporal_block = ""
        else:
            from core.kg.temporal import render_temporal_block
            self._temporal_block = render_temporal_block(self.edital_id)

        # Substrato do pitch (context-stuffing do nó do fundo): tese + portfólio +
        # estágio/setor/ticket. Pequeno (~1 entry) → stuffing bate retrieval. Vazio
        # fora do modo pitch. Spec §3.5 (escrita outbound condicionada pelo nó do KG).
        self._pitch_target_context = (
            self._build_pitch_target_context() if self.mode == "pitch" else ""
        )

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

        # Outline: pitch usa o default de captação (fundo não tem wiki/PDF de
        # outline); evento usa DB → wiki page → LLM.
        if not self._proposal_outline:
            if self.mode == "pitch":
                self._proposal_outline = self._default_pitch_outline()
            else:
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
        # Pitch (entidade): sem análogos de edital — o substrato é o nó do fundo,
        # injetado como contexto, não chunks. Devolve só o próprio id.
        if self.mode == "pitch":
            return [self.edital_id]
        try:
            from core.services.kg_match_service import KGMatchService
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
        pending = row.get("pending_user_input")
        self._pending_user_input = pending if isinstance(pending, dict) else None
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
        # turn_count = número de turnos lógicos do usuário (1 por par
        # user+assistant). NÃO usar max(turn_index): o índice físico no DB é
        # remapeado em _persist_turn (user=2N-1, assistant=2N) por causa da
        # UNIQUE constraint (session_id, turn_index). Ler o máximo desse
        # índice e armazená-lo em _turn_count fazia o contador dobrar a cada
        # reload (sequência 1, 3, 7, 15, 31, 63… porque cada request cria uma
        # WritingSession nova). Resultado: COMPRESS_THRESHOLD perdia o sentido
        # (sempre passava cedo demais), e o gate real virava o len(_history)
        # de _compress_history. Bug latente do M8.
        self._turn_count = sum(1 for t in turns if t["role"] == "user")

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

        Aplica o mesmo filtro de versões usado no chunker pra evitar
        concatenar 4 versões do FAQ + 4 rerratificações no contexto do outline.
        A função vive em `pipeline.adapters.finep` (fonte de verdade do dedup
        de versões); import lazy pra não puxar a stack de pipeline no boot.
        """
        # Import lazy — evita carregar pipeline/pdfplumber no boot do módulo.
        from pipeline.adapters.finep import _filter_to_latest_versions

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
        wiki_file = wiki_page_path(edital_id)
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

    @staticmethod
    def _default_pitch_outline() -> list[str]:
        """Outline do gênero pitch/one-pager outbound (captação)."""
        return [
            "1. Problema",
            "2. Solução e diferencial tecnológico",
            "3. Mercado (TAM/SAM/SOM)",
            "4. Tração",
            "5. Time",
            "6. Fit com a tese do fundo",
            "7. Ask e uso dos recursos",
        ]

    def _writer_system(self) -> str:
        """System prompt do agente conforme o modo (proposta vs pitch)."""
        return PITCH_WRITER_AGENT_SYSTEM if self.mode == "pitch" else WRITER_AGENT_SYSTEM

    def _build_pitch_target_context(self) -> str:
        """Bloco de contexto do fundo-alvo (context-stuffing do nó do KG).

        Carrega o nó de `investidores.json` por id (`investidor:<slug>`) e o
        serializa em texto pro prompt — tese, temas, setores, estágio, ticket,
        portfólio, co-investidores. Vazio (com aviso) se o fundo não for achado;
        a sessão não quebra — opera só com o perfil da startup.
        """
        from core import kg_store  # lazy: evita custo no boot do módulo
        try:
            invs = {i["id"]: i for i in kg_store.load_investidores()}
        except Exception as e:
            logger.warning("[%s] load_investidores falhou: %s", self.session_id, e)
            return ""
        fund = invs.get(self.edital_id)
        if not fund:
            logger.warning(
                "[%s] fundo %s não encontrado em investidores.json — pitch sem nó-alvo",
                self.session_id, self.edital_id,
            )
            return ""

        lines = [f"FUNDO-ALVO: {fund.get('name', self.edital_id)}"]
        if fund.get("tese"):
            lines.append(f"Tese: {fund['tese']}")
        if fund.get("tese_themes"):
            lines.append(f"Temas da tese: {', '.join(fund['tese_themes'])}")
        if fund.get("setores"):
            lines.append(f"Setores: {', '.join(fund['setores'])}")
        if fund.get("estagio_alvo"):
            lines.append(f"Estágio alvo: {', '.join(fund['estagio_alvo'])}")
        ticket = fund.get("ticket_range")
        if ticket:
            lo, hi = ticket.get("min_brl"), ticket.get("max_brl")
            if lo or hi:
                lines.append(f"Ticket (BRL): {lo or '?'}–{hi or '?'}")
        if fund.get("lead_follow"):
            lines.append(f"Lead/follow: {fund['lead_follow']}")
        if fund.get("portfolio"):
            lines.append(f"Portfólio: {', '.join(fund['portfolio'])}")
        if fund.get("co_investidores"):
            lines.append(f"Co-investidores: {', '.join(fund['co_investidores'])}")
        if fund.get("site"):
            lines.append(f"Site: {fund['site']}")
        return "FUNDO-ALVO (use para ancorar o fit; não invente tese):\n" + "\n".join(lines)

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
            "session_id":         self.session_id,
            "edital_id":          self.edital_id,
            "section_titles":     self._proposal_outline,
            "has_documents":      has_documents,
            "turn_count":         self._turn_count,
            "created_at":         self.created_at,
            # Sprint 2 do Cenário B: se há pergunta pendente do agente, frontend
            # renderiza prompt destacado ao retomar a sessão (não só após turn).
            "pending_user_input": self._pending_user_input,
        }

    def turn(self, user_message: str, section_hint: str | None = None) -> dict:
        """Processa um turno de escrita via agente (único path).

        Todo turno roda o agente com tools (search_edital, save_draft com
        critic, coerência interna). O legacy 1-shot por regex `<draft>` foi
        aposentado (Front 1): não há mais branching por feature flag. Se o
        agente falhar (stop_reason=error), retorna erro amigável — sem cair
        em legacy.
        """
        self._turn_count += 1
        user_turn_index = self._turn_count
        logger.info("[%s] Turno %d", self.session_id, self._turn_count)

        # Consumir pending_user_input se houver: o user_message ATUAL responde a
        # ela. Limpamos antes de processar pra evitar reapresentar a pergunta.
        had_pending = self._pending_user_input is not None
        self._pending_user_input = None

        try:
            if self._turn_count > COMPRESS_THRESHOLD:
                self._compress_history()

            result = self._turn_agent(user_message, section_hint, user_turn_index)

            # Persiste pending_user_input se a tool request_user_info disparou
            # neste turn; OU limpa no DB se consumimos um pendente acima.
            if self._pending_user_input is not None or had_pending:
                self._save_pending_user_input(self._pending_user_input)

            return result
        except Exception as e:
            logger.error("[%s] Erro no turno %d: %s", self.session_id, self._turn_count, e)
            return self._error_result(str(e), "INTERNAL_ERROR")

    def _turn_agent(
        self,
        user_message: str,
        section_hint: str | None,
        user_turn_index: int,
    ) -> dict:
        """Path de escrita: run_agent + tools (search_edital, search_library,
        read_section, read_full_proposal, save_draft, request_user_info, ...).

        Características:
          • Sem RAG eager — o agente decide via search_edital / search_library
          • Sem retrieval auto de library — idem
          • save_draft tool persiste a seção (com critic) como side effect
          • request_user_info emite sinal estruturado pro frontend quando falta
            info concreta do usuário
          • mentions resolvem antes (intenção explícita do usuário)
        """
        from core.llm.agent_runtime import resolve_agent_provider, run_agent
        from core.llm.agent_tools import build_writing_tools

        mentions_context = self._resolve_mentions(user_message)
        messages = self._build_agent_initial_messages(
            user_message, section_hint, mentions_context,
        )
        tools = build_writing_tools(self)

        provider, model = resolve_agent_provider("anthropic", ANTHROPIC_MODEL_AGENT)
        result = run_agent(
            system=self._writer_system(),
            initial_messages=messages,
            tools=tools,
            model=model,
            provider=provider,
            max_steps=AGENT_MAX_STEPS,
            reflect_every=3,
        )

        if result.stop_reason == "error":
            self._turn_count -= 1
            return self._error_result(
                "Agente falhou ao processar — tente novamente em instantes.",
                "AGENT_ERROR",
            )

        assistant_text = result.final_text or ""

        # Reconstrói tool_use trace para persistência: [{id, name, input, output}]
        # pareando tool_use blocks com seus tool_result subsequentes no `steps`.
        tool_trace = self._extract_tool_trace(result.steps)

        # Atualiza histórico in-memory (sem o trace — chat só vê o texto final).
        self._history.append({"role": "user",      "content": user_message})
        self._history.append({"role": "assistant", "content": assistant_text})

        # Persiste turn: user sem tool_use, assistant com tool_use (mesmo se vazio,
        # pra distinguir de legacy onde tool_use é NULL).
        # tokens: soma input+output de TODAS as chamadas LLM do agente neste turn
        # (result.usage agrega o loop inteiro) — custo/turno fica mensurável. Vai
        # na row assistant; a row user fica com tokens NULL (não há custo nela).
        turn_tokens = (result.usage.get("input_tokens", 0)
                       + result.usage.get("output_tokens", 0)) or None
        self._persist_turn(user_turn_index, "user", user_message, section_hint)
        self._persist_turn(
            user_turn_index, "assistant", assistant_text, section_hint,
            tool_use=tool_trace, tokens=turn_tokens,
        )

        return {
            "session_id":         self.session_id,
            "assistant_message":  assistant_text,
            "draft_content":      None,  # save_draft tool já persistiu via side effect
            "pending_user_input": self._pending_user_input,
            "turn_number":        self._turn_count,
            "success":            True,
            "error":              None,
            "tool_trace":         tool_trace,
        }

    @staticmethod
    def _extract_tool_trace(steps: list) -> list[dict]:
        """Extrai trace persistível dos steps de run_agent.

        Pareia tool_use (vindos do step llm) com tool_result (vindos do step tool)
        por ordem — o run_agent garante que a sequência é llm → tool* → llm → ...
        e que cada tool_use é seguido por um step tool com o mesmo nome.
        """
        # Mapa tool_use_id → input (vem dos steps kind="llm" em tool_uses)
        use_inputs: dict[str, dict] = {}
        for s in steps:
            if s.kind == "llm":
                for use in s.tool_uses:
                    use_inputs[use["id"]] = use

        trace: list[dict] = []
        for s in steps:
            if s.kind == "tool":
                # Tenta achar o input correspondente pelo nome — em sequência
                # 1:1 isto é determinístico. Caso múltiplas tools com mesmo nome,
                # pegamos a primeira ainda não consumida.
                matched_id = None
                for uid, use in use_inputs.items():
                    if use["name"] == s.name:
                        matched_id = uid
                        break
                if matched_id:
                    use_inputs.pop(matched_id, None)
                trace.append({
                    "id": matched_id or "",
                    "name": s.name,
                    "input": s.input,
                    "output": s.output,
                })
        return trace

    def _save_pending_user_input(self, value: dict | None) -> None:
        """Persiste writing_sessions.pending_user_input (best-effort)."""
        try:
            self._db.table("writing_sessions").update({
                "pending_user_input": value,
            }).eq("id", self.session_id).execute()
        except Exception as e:
            logger.warning(
                "[%s] Falha ao persistir pending_user_input: %s",
                self.session_id, e,
            )

    def _build_agent_initial_messages(
        self,
        user_message: str,
        section_hint: str | None,
        mentions_context: str,
    ) -> list[dict]:
        """Prefixo estável + mensagem atual, no formato esperado pelo Anthropic
        (sem system; ele vai como parâmetro top-level do run_agent).

        A ordem espelha `_build_messages` (legacy), com 2 diferenças:
          • Sem RAG / sem retrieval auto de library: o agente busca via tools
          • Mantém perfil + library_anexada + insights + summary + history
            antes da mensagem para preservar prompt caching.
        """
        messages: list[dict] = [
            {"role": "user", "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
        ]
        if self._pitch_target_context:
            messages.append({"role": "user", "content": self._pitch_target_context})
        if self._temporal_block:
            messages.append({"role": "user", "content": self._temporal_block})
        if self._library_context:
            messages.append({"role": "user", "content": self._library_context})
        if self._reflection_insights_context:
            messages.append({"role": "user", "content": self._reflection_insights_context})
        if self._history_summary:
            messages.append({"role": "user", "content": self._history_summary})

        messages.extend(self._history)

        if mentions_context:
            messages.append({"role": "user", "content": mentions_context})
        if section_hint:
            messages.append({"role": "user", "content": f"[Seção ativa: {section_hint}]"})

        messages.append({"role": "user", "content": user_message})
        return messages

    def _persist_turn(
        self,
        turn_index: int,
        role: str,
        content: str,
        section_hint: str | None,
        tool_use: list[dict] | None = None,
        tokens: int | None = None,
    ) -> None:
        """INSERT em session_turns. Falhas são logadas mas não interrompem o turno.

        O par (user, assistant) compartilha o turn_index — distinguidos por role.
        Como a constraint UNIQUE é (session_id, turn_index), usamos índices
        ímpares e pares para evitar colisão: user=2k-1, assistant=2k.

        `tool_use` é populado APENAS no turn assistant do path agente — lista de
        {id, name, input, output}. NULL significa turn legacy (1-shot). Lista
        vazia significa turn de agente que não usou nenhuma tool.
        """
        # Re-mapeia para garantir unicidade no DB:
        # turn_index lógico N → user em 2N-1, assistant em 2N.
        if role == "user":
            db_index = turn_index * 2 - 1
        elif role == "assistant":
            db_index = turn_index * 2
        else:
            db_index = turn_index

        payload: dict = {
            "session_id": self.session_id,
            "turn_index": db_index,
            "role": role,
            "content": content,
            "section_hint": section_hint,
        }
        if tool_use is not None:
            payload["tool_use"] = tool_use
        if tokens is not None:
            payload["tokens"] = tokens

        try:
            self._db.table("session_turns").insert(payload).execute()
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
        starter_system = (
            _PITCH_SECTION_STARTER_SYSTEM if self.mode == "pitch" else _SECTION_STARTER_SYSTEM
        )
        messages = [
            {"role": "system", "content": starter_system},
            {"role": "user",   "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
        ]

        # Pitch: substrato é o nó do fundo (já carregado), não chunks de edital.
        if self.mode == "pitch":
            context_text = self._pitch_target_context
            alvo = "o fundo-alvo"
        else:
            alvo = "o edital"
            try:
                chunks = retrieve_chunks(
                    self._db, self._scope_edital_ids, query=section_title, k=3,
                )
                context_text = (
                    format_chunks_for_prompt(chunks, edital_ids=self._scope_edital_ids)
                    if chunks else ""
                )
            except Exception as e:
                logger.warning(
                    "[%s] get_section_starter: retrieve_chunks falhou: %s",
                    self.session_id, e,
                )
                context_text = ""

        if context_text:
            messages.append({"role": "user", "content": context_text})

        messages.append({
            "role": "user",
            "content": (
                f"Gere uma mensagem de boas-vindas curta (máx. 3 frases) para a seção "
                f"'{section_title}'. Mencione o que deve conter e como o perfil da empresa "
                f"se conecta a {alvo}. Termine com uma sugestão de por onde começar."
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
            from core.llm.llm_client import make_client
            client = make_client(api_key=OPENAI_API_KEY)
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

    Retorna registros leves: id, edital_id, kind, title, status, created_at,
    updated_at, turn_count. O turn_count é derivado de um SELECT count agregado
    em session_turns por sessão.

    `kind`/`title` foram adicionados na migration 020 (lista unificada de
    conversas, fase 2). Consumidores antigos (GET /writing/sessions) ignoram os
    campos extras — não há quebra.
    """
    query = (
        db.table("writing_sessions")
        .select("id, edital_id, kind, title, status, created_at, updated_at")
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


# =============================================================================
# Conversations (spec frontend chat-first, fase 2)
# =============================================================================
# As "conversations" são as mesmas tabelas writing_sessions/session_turns; o
# `kind` distingue o sabor. Os helpers abaixo servem o router /conversations
# e a persistência do front door autenticado — entradas heterogêneas (msg, diff,
# radar) num único transcript ordenado por turn_index.


def get_conversation(db: Client, session_id: str, workspace_id: str) -> dict | None:
    """Header de uma conversa (qualquer kind) + entradas ordenadas por turn_index.

    Retorna None se a sessão não existir OU não pertencer ao workspace (RLS já
    protege; validamos explicitamente também — defesa em profundidade).

    O shape de cada entrada espelha o `Entry` da spec: id, turn_index,
    entry_kind, role, content, payload. As entradas vêm direto de session_turns
    (a remap de turn_index user=2N-1/assistant=2N feita pela WritingSession é
    transparente aqui — ordenamos pelo índice físico, que mantém a ordem).
    """
    result = (
        db.table("writing_sessions")
        .select("id, workspace_id, edital_id, kind, title, status, created_at, updated_at")
        .eq("id", session_id)
        .maybe_single()
        .execute()
    )
    row = result.data if result else None
    if not row or row["workspace_id"] != workspace_id:
        return None

    turns = (
        db.table("session_turns")
        .select("id, turn_index, entry_kind, role, content, payload")
        .eq("session_id", session_id)
        .order("turn_index", desc=False)
        .order("id", desc=False)
        .execute()
    )
    entries = [
        {
            "id": t["id"],
            "turn_index": t["turn_index"],
            "entry_kind": t.get("entry_kind") or "msg",
            "role": t["role"],
            "content": t.get("content") or "",
            "payload": t.get("payload"),
        }
        for t in (turns.data or [])
    ]
    return {
        "session_id": row["id"],
        "kind": row.get("kind") or "writing",
        "title": row.get("title"),
        "edital_id": row.get("edital_id"),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "entries": entries,
    }


def _next_turn_index(db: Client, session_id: str) -> int:
    """Próximo turn_index livre da conversa (max + 1, ou 1 se vazia).

    Para o transcript do front door tratamos turn_index como um índice físico
    monotônico de entrada (sem o pareamento user=2N-1/assistant=2N da escrita) —
    cada entrada appendada ganha o próximo inteiro, preservando a ordem cronológica.
    """
    res = (
        db.table("session_turns")
        .select("turn_index")
        .eq("session_id", session_id)
        .order("turn_index", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return (rows[0]["turn_index"] + 1) if rows else 1


def append_entry(
    db: Client,
    session_id: str,
    workspace_id: str,
    entry_kind: str,
    payload: dict,
) -> dict | None:
    """Appenda uma entrada não-msg (radar/diff) ao transcript. Retorna a entrada
    criada (shape Entry) ou None se a sessão não pertencer ao workspace.

    role='assistant' e content='' por convenção (a entrada vive no payload —
    spec, "modelo de dados"). turn_index é o próximo índice físico livre.
    """
    owner = (
        db.table("writing_sessions")
        .select("id, workspace_id")
        .eq("id", session_id)
        .maybe_single()
        .execute()
    )
    row = owner.data if owner else None
    if not row or row["workspace_id"] != workspace_id:
        return None

    turn_index = _next_turn_index(db, session_id)
    inserted = (
        db.table("session_turns")
        .insert({
            "session_id": session_id,
            "turn_index": turn_index,
            "role": "assistant",
            "content": "",
            "entry_kind": entry_kind,
            "payload": payload,
        })
        .execute()
    )
    new_row = inserted.data[0] if inserted.data else None
    if not new_row:
        return None
    return {
        "id": new_row["id"],
        "turn_index": new_row["turn_index"],
        "entry_kind": new_row.get("entry_kind") or entry_kind,
        "role": new_row["role"],
        "content": new_row.get("content") or "",
        "payload": new_row.get("payload"),
    }


def update_entry_payload(
    db: Client,
    session_id: str,
    entry_id: int,
    workspace_id: str,
    payload: dict,
) -> dict | None:
    """Substitui o payload de uma entrada (caso de uso: status do diff
    pending→accepted/dismissed). Retorna a entrada atualizada ou None se a
    entrada não existir / não pertencer à sessão / sessão de outro workspace.
    """
    owner = (
        db.table("writing_sessions")
        .select("id, workspace_id")
        .eq("id", session_id)
        .maybe_single()
        .execute()
    )
    row = owner.data if owner else None
    if not row or row["workspace_id"] != workspace_id:
        return None

    updated = (
        db.table("session_turns")
        .update({"payload": payload})
        .eq("id", entry_id)
        .eq("session_id", session_id)
        .execute()
    )
    new_row = updated.data[0] if updated.data else None
    if not new_row:
        return None  # entry_id inexistente ou de outra sessão
    return {
        "id": new_row["id"],
        "turn_index": new_row["turn_index"],
        "entry_kind": new_row.get("entry_kind") or "msg",
        "role": new_row["role"],
        "content": new_row.get("content") or "",
        "payload": new_row.get("payload"),
    }


def persist_frontdoor_turn(
    db: Client,
    workspace_id: str,
    user_message: str,
    assistant_message: str,
    profile_diff: list[dict] | None,
    session_id: str | None = None,
) -> dict:
    """Persiste um turno do front door (usuário logado) e devolve session_id +
    ids das entradas criadas.

    Cria a conversa (kind='frontdoor') no primeiro turno; reusa a existente nos
    seguintes. Grava: turno do usuário (msg) + resposta do assistente (msg) +,
    se houver diff, a proposta como entrada `diff` (payload={items, status,
    origin}). O id do diff volta para o front fazer o PATCH no aceite/descarte.

    Levanta em falha de DB — o caller (router) decide engolir o erro (a conversa
    vale mais que o histórico). NÃO é best-effort por dentro de propósito: assim
    o caller consegue logar com contexto e devolver a resposta mesmo assim.
    """
    if not session_id:
        title = user_message.strip()[:60]
        created = (
            db.table("writing_sessions")
            .insert({
                "workspace_id": workspace_id,
                "edital_id": None,
                "kind": "frontdoor",
                "title": title,
                "status": "active",
                "proposal_outline": [],
                "section_drafts": {},
            })
            .execute()
        )
        if not created.data:
            raise RuntimeError("Falha ao criar conversa frontdoor")
        session_id = created.data[0]["id"]

    base_index = _next_turn_index(db, session_id)
    entry_ids: dict[str, int | None] = {}

    user_row = (
        db.table("session_turns")
        .insert({
            "session_id": session_id,
            "turn_index": base_index,
            "role": "user",
            "content": user_message,
            "entry_kind": "msg",
        })
        .execute()
    )
    entry_ids["user"] = user_row.data[0]["id"] if user_row.data else None

    assistant_row = (
        db.table("session_turns")
        .insert({
            "session_id": session_id,
            "turn_index": base_index + 1,
            "role": "assistant",
            "content": assistant_message,
            "entry_kind": "msg",
        })
        .execute()
    )
    entry_ids["assistant"] = assistant_row.data[0]["id"] if assistant_row.data else None

    if profile_diff:
        diff_row = (
            db.table("session_turns")
            .insert({
                "session_id": session_id,
                "turn_index": base_index + 2,
                "role": "assistant",
                "content": "",
                "entry_kind": "diff",
                "payload": {
                    "items": profile_diff,
                    "status": "pending",
                    "origin": "turn",
                },
            })
            .execute()
        )
        entry_ids["diff"] = diff_row.data[0]["id"] if diff_row.data else None

    return {"session_id": session_id, "entry_ids": entry_ids}
