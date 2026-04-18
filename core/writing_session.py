"""
Writing Session (Radar de Editais)

Gerencia uma sessão conversacional de escrita de proposta para um edital específico.

Fluxo por turno:
  1. Router LLM — recebe mensagem do usuário + títulos das seções disponíveis
                  → decide quais seções buscar (chamada leve, modelo pequeno)
  2. SectionRetriever — carrega conteúdo das seções selecionadas
  3. Writer LLM — gera resposta com: perfil (estático) + seções + histórico comprimido

Gerenciamento de histórico:
  - Mantém os últimos HISTORY_WINDOW turnos verbatim
  - Após COMPRESS_THRESHOLD turnos, comprime os mais antigos em resumo
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional

import requests

from domain.user_profile import CompanyProfile
from core.section_retriever import SectionRetriever
from core.live_fetcher import LiveFetcher

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LLM_BACKEND   = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL  = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

HISTORY_WINDOW      = 6   # turnos recentes mantidos verbatim
COMPRESS_THRESHOLD  = 10  # a partir deste número de turnos, comprime os mais antigos

# =============================================================================
# PROMPTS
# =============================================================================

ROUTER_SYSTEM_PROMPT = """Você é um navegador de documentos.
Dado o pedido do usuário e a lista de seções disponíveis no edital,
retorne um JSON array com os títulos das seções necessárias para responder ao pedido.
Inclua apenas seções diretamente relevantes. Retorne [] se nenhuma seção específica for necessária.
Responda APENAS com o JSON array, sem explicações."""

WRITER_SYSTEM_PROMPT = """Você é um especialista em redação de propostas para editais de fomento no Brasil.
Seu papel é ajudar o usuário a escrever uma proposta técnica de alta qualidade para o edital selecionado.

Diretrizes:
- Responda diretamente ao pedido do usuário com conteúdo concreto e redigido.
- Baseie-se nas informações do edital e no perfil da empresa fornecidos.
- Use Markdown para estruturar o texto quando produzir trechos da proposta.
- Quando produzir um trecho, seja propositivo: não diga "poderíamos fazer", diga "faremos".
- Nunca invente dados numéricos que não estejam no perfil ou no edital.
- Use [COMPLETAR: descrição] para lacunas que dependem de informação do usuário.
- Para perguntas conceituais ou de esclarecimento, responda de forma direta sem redigir trecho.
- Quando uma seção ativa for indicada, concentre a resposta nessa seção específica."""

SECTION_STARTER_SYSTEM = """Você é um especialista em redação de propostas para editais de fomento no Brasil.
Gere uma mensagem de boas-vindas curta (máx. 3 frases) para introduzir o usuário à seção indicada.
Mencione o que essa seção deve conter e como o perfil da empresa se conecta ao edital.
Termine com uma pergunta ou sugestão de por onde começar. Seja direto e propositivo."""

COMPRESS_SYSTEM_PROMPT = """Você é um assistente que resume conversas.
Resuma os turnos abaixo em um parágrafo conciso (máx. 200 palavras),
preservando: decisões tomadas, trechos aprovados pelo usuário e informações adicionais fornecidas.
Responda apenas com o resumo, sem prefácios."""


# =============================================================================
# WRITING SESSION
# =============================================================================

class WritingSession:
    """
    Sessão conversacional de escrita de proposta.

    Args:
        edital_id: ID do edital selecionado
        profile: Perfil da empresa
        session_id: UUID da sessão (gerado automaticamente se omitido)
        llm_backend: "ollama" | "openai"
        model: Nome do modelo a usar
    """

    def __init__(
        self,
        edital_id: str,
        profile: CompanyProfile,
        edital_url: str = "",
        session_id: Optional[str] = None,
        llm_backend: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.session_id   = session_id or str(uuid.uuid4())
        self.edital_id    = edital_id
        self.profile      = profile
        self.backend      = llm_backend or LLM_BACKEND
        self.model        = model or (OLLAMA_MODEL if self.backend == "ollama" else OPENAI_MODEL)
        self.created_at   = datetime.now().isoformat()

        self._retriever   = SectionRetriever()
        self._history: list[dict] = []       # {"role": "user"|"assistant", "content": "..."}
        self._history_summary: str = ""      # resumo comprimido dos turnos antigos
        self._turn_count  = 0
        self._content_source = "section_index"

        # Cache estático: perfil e metadados do edital (imutáveis na sessão)
        self._profile_context = profile.to_context()
        self._section_titles  = self._retriever.list_sections(edital_id)

        # Se não há section_index local, busca conteúdo ao vivo
        if not self._section_titles and edital_url:
            logger.info(f"[{self.session_id}] section_index ausente — iniciando live fetch")
            LiveFetcher().fetch_and_save(edital_id, edital_url)
            self._section_titles = self._retriever.list_sections(edital_id)
            self._content_source = "live_fetch"

        logger.info(
            f"WritingSession {self.session_id} | edital={edital_id} "
            f"| {len(self._section_titles)} seções [{self._content_source}] "
            f"| {self.backend}/{self.model}"
        )

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def turn(self, user_message: str, section_hint: Optional[str] = None) -> dict:
        """
        Processa um turno da conversa.

        Args:
            user_message: Mensagem do usuário.
            section_hint: Título da seção ativa no frontend (opcional).

        Returns:
            {session_id, assistant_message, sections_used, turn_number, success, error}
        """
        self._turn_count += 1
        logger.info(f"[{self.session_id}] Turno {self._turn_count}: {user_message[:80]}...")

        try:
            # 1. Router: decide quais seções buscar
            sections_to_fetch = self._route_sections(user_message, section_hint)

            # 2. Retriever: carrega conteúdo das seções selecionadas
            sections_text = self._retriever.get_sections(self.edital_id, sections_to_fetch)

            # 3. Comprime histórico se necessário
            if self._turn_count > COMPRESS_THRESHOLD:
                self._compress_history()

            # 4. Writer: gera resposta
            messages = self._build_writer_messages(user_message, sections_text, section_hint)
            success, response_text, error_type = self._call_llm(messages)

            if not success:
                return self._error_result(response_text, error_type)

            # 5. Atualiza histórico
            self._history.append({"role": "user",      "content": user_message})
            self._history.append({"role": "assistant", "content": response_text})

            return {
                "session_id":        self.session_id,
                "assistant_message": response_text,
                "sections_used":     sections_to_fetch,
                "turn_number":       self._turn_count,
                "success":           True,
                "error":             None,
            }

        except Exception as e:
            logger.error(f"[{self.session_id}] Erro no turno {self._turn_count}: {e}")
            return self._error_result(str(e), "INTERNAL_ERROR")

    def get_section_starter(self, section_title: str) -> str:
        """
        Gera mensagem inicial para uma seção específica da proposta.
        Busca fatos relevantes do edital para contextualizar.
        """
        section_text = self._retriever.get_sections(self.edital_id, [section_title])

        user_content = (
            f"Seção da proposta: {section_title}\n\n"
            f"Perfil da empresa:\n{self._profile_context[:800]}\n\n"
        )
        if section_text:
            user_content += f"Conteúdo do edital para esta seção:\n{section_text[:1200]}"

        messages = [
            {"role": "system", "content": SECTION_STARTER_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        success, text, _ = self._call_llm(messages, temperature=0.4, max_tokens=300)
        if success:
            return text
        return (
            f"Vamos trabalhar na seção **{section_title}**. "
            "Como posso ajudar você a começar?"
        )

    def get_info(self) -> dict:
        """Retorna metadados da sessão (para o endpoint /writing/start)."""
        return {
            "session_id":      self.session_id,
            "edital_id":       self.edital_id,
            "section_titles":  self._section_titles,
            "content_source":  self._content_source,
            "turn_count":      self._turn_count,
            "created_at":      self.created_at,
        }

    # ------------------------------------------------------------------
    # Router LLM
    # ------------------------------------------------------------------

    def _route_sections(self, user_message: str, section_hint: Optional[str] = None) -> list[str]:
        """
        Chama o Router LLM para decidir quais seções são relevantes ao turno.
        Se section_hint fornecido, prioriza essa seção.
        Retorna lista de títulos. Fallback: retorna todas as seções disponíveis.
        """
        if not self._section_titles:
            return []

        sections_list = "\n".join(f"- {t}" for t in self._section_titles)
        hint_line = f"Seção ativa na interface: {section_hint}\n" if section_hint else ""
        user_content = (
            f"Seções disponíveis no edital:\n{sections_list}\n\n"
            f"{hint_line}"
            f"Pedido do usuário: {user_message}"
        )

        messages = [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ]

        success, text, _ = self._call_llm(messages, temperature=0.0, max_tokens=200)

        if not success:
            logger.warning(f"[{self.session_id}] Router LLM falhou — usando todas as seções")
            return self._section_titles

        selected = self._parse_json_list(text)
        if not selected:
            logger.debug(f"[{self.session_id}] Router retornou [] — sem seções específicas")
            return []

        # Valida: mantém apenas títulos que realmente existem
        valid = [t for t in selected if t in self._section_titles]
        if not valid:
            # Tenta matching parcial (Router pode retornar título levemente diferente)
            valid = [
                available for available in self._section_titles
                if any(sel.lower() in available.lower() or available.lower() in sel.lower()
                       for sel in selected)
            ]

        logger.info(f"[{self.session_id}] Router selecionou: {valid}")
        return valid or self._section_titles

    # ------------------------------------------------------------------
    # Compressão de histórico
    # ------------------------------------------------------------------

    def _compress_history(self) -> None:
        """
        Comprime os turnos mais antigos em um resumo de texto.
        Mantém os últimos HISTORY_WINDOW turnos verbatim.
        """
        if len(self._history) <= HISTORY_WINDOW * 2:
            return

        to_compress = self._history[: -(HISTORY_WINDOW * 2)]
        self._history  = self._history[-(HISTORY_WINDOW * 2):]

        turns_text = "\n".join(
            f"{msg['role'].upper()}: {msg['content']}" for msg in to_compress
        )
        compress_input = (
            f"Turnos anteriores da conversa de escrita de proposta:\n\n{turns_text}"
        )

        messages = [
            {"role": "system", "content": COMPRESS_SYSTEM_PROMPT},
            {"role": "user",   "content": compress_input},
        ]

        success, summary, _ = self._call_llm(messages, temperature=0.3, max_tokens=300)

        if success and summary.strip():
            prefix = f"[Resumo dos turnos anteriores: {summary.strip()}]\n\n"
            self._history_summary = prefix + self._history_summary
            logger.info(f"[{self.session_id}] Histórico comprimido ({len(to_compress)} msgs → resumo)")
        else:
            # Fallback: descarta os turnos antigos sem resumo
            logger.warning(f"[{self.session_id}] Compressão falhou — turnos antigos descartados")

    # ------------------------------------------------------------------
    # Montagem do prompt do Writer LLM
    # ------------------------------------------------------------------

    def _build_writer_messages(self, user_message: str, sections_text: str, section_hint: Optional[str] = None) -> list[dict]:
        """
        Monta a lista de mensagens para o Writer LLM.

        Estrutura (do mais estático ao mais dinâmico):
          system  : WRITER_SYSTEM_PROMPT
          user    : perfil da empresa (imutável na sessão → cacheable)
          user    : seções do edital (varia por turno)
          ...     : histórico (resumo + janela recente)
          user    : mensagem atual
        """
        messages: list[dict] = [
            {"role": "system", "content": WRITER_SYSTEM_PROMPT},
            {"role": "user",   "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
        ]

        if sections_text:
            messages.append({
                "role":    "user",
                "content": f"SEÇÕES RELEVANTES DO EDITAL:\n{sections_text}",
            })

        if self._history_summary:
            messages.append({
                "role":    "user",
                "content": self._history_summary,
            })

        if section_hint:
            messages.append({
                "role": "user",
                "content": f"[Seção ativa: {section_hint}]",
            })

        messages.extend(self._history)
        messages.append({"role": "user", "content": user_message})

        return messages

    # ------------------------------------------------------------------
    # Chamadas LLM
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        messages: list[dict],
        temperature: float = 0.5,
        max_tokens: int = 2000,
    ) -> tuple[bool, str, Optional[str]]:
        if self.backend == "ollama":
            return self._call_ollama(messages, temperature, max_tokens)
        return self._call_openai(messages, temperature, max_tokens)

    def _call_ollama(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> tuple[bool, str, Optional[str]]:
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model":   self.model,
                    "messages": messages,
                    "stream":  False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
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
            logger.error(f"Erro Ollama: {e}")
            return False, str(e), "UNKNOWN_ERROR"

    def _call_openai(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> tuple[bool, str, Optional[str]]:
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
            logger.error(f"Erro OpenAI: {e}")
            return False, str(e), "API_ERROR"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_json_list(self, text: str) -> list[str]:
        """Extrai JSON array da resposta do Router LLM com tolerância a ruído."""
        text = text.strip()
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return [str(i) for i in result]
        except json.JSONDecodeError:
            pass
        match = re.search(r"\[.*?\]", text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
                if isinstance(result, list):
                    return [str(i) for i in result]
            except json.JSONDecodeError:
                pass
        return []

    def _error_result(self, message: str, error_type: Optional[str]) -> dict:
        return {
            "session_id":        self.session_id,
            "assistant_message": f"Erro ao processar: {message}",
            "sections_used":     [],
            "turn_number":       self._turn_count,
            "success":           False,
            "error":             message,
            "error_type":        error_type,
        }
