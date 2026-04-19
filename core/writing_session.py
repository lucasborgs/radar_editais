"""
Writing Session (Radar de Editais) — NotebookLM style

Fluxo:
  1. __init__: carrega todos os PDFs do edital do disco (raw sources)
               gera o outline da proposta via LLM (1 chamada)
  2. turn: Writer LLM recebe prefixo estático (perfil + documentos) + histórico + mensagem
           O prefixo é fixo em todos os turnos → prompt caching elimina custo de re-processamento

Prompt caching:
  - Gemini Flash: context caching nativo
  - gpt-4o-mini: caching automático para prompts > 1024 tokens
  O prefixo [system + perfil + documentos] deve sempre vir primeiro e permanecer idêntico
  entre turnos para que o cache seja aproveitado.

Gerenciamento de histórico:
  - Mantém os últimos HISTORY_WINDOW turnos verbatim
  - Comprime os mais antigos em resumo após COMPRESS_THRESHOLD turnos
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config import FINEP_PDFS_DIR, KG_WIKI_DIR
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LLM_BACKEND    = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

HISTORY_WINDOW     = 6
COMPRESS_THRESHOLD = 10

# PDFs a ignorar (mesma lista do etl_process)
_SKIP_KEYWORDS = [
    "minuta", "declaracao", "carta_de_manifestacao", "faq",
    "apresentacao", "resultado", "oficio", "telas_fap",
    "orientacoes_para_apresentacao", "tabela_com_requisitos",
    "orientacoes_para_despesas", "relatorio_parcial",
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
- Quando uma seção ativa for indicada, concentre a resposta nessa seção."""

COMPRESS_SYSTEM = """Resuma os turnos abaixo em um parágrafo conciso (máx. 200 palavras).
Preserve: decisões tomadas, trechos aprovados pelo usuário e informações adicionais fornecidas.
Responda apenas com o resumo."""


# =============================================================================
# WRITING SESSION
# =============================================================================

class WritingSession:
    """
    Sessão de escrita no estilo NotebookLM.

    Os PDFs do edital são carregados uma vez no __init__ e mantidos como
    prefixo estático em todos os turnos — o prompt caching do modelo evita
    re-processamento a cada turno.
    """

    def __init__(
        self,
        edital_id: str,
        profile: CompanyProfile,
        session_id: Optional[str] = None,
        llm_backend: Optional[str] = None,
        model: Optional[str] = None,
        library_items: Optional[list[dict]] = None,
    ):
        self.session_id  = session_id or str(uuid.uuid4())
        self.edital_id   = edital_id
        self.profile     = profile
        self.backend     = llm_backend or LLM_BACKEND
        self.model       = model or (OLLAMA_MODEL if self.backend == "ollama" else OPENAI_MODEL)
        self.created_at  = datetime.now().isoformat()

        self._history: list[dict] = []
        self._history_summary: str = ""
        self._turn_count = 0
        self._doc_sections: dict[str, str] = {}

        # Prefixo estático — enviado identicamente em todos os turnos (prompt caching)
        self._profile_context  = profile.to_context()
        self._documents_text   = self._load_documents(edital_id)
        self._library_context  = self._build_library_context(library_items or [])

        # Outline da proposta: lê da wiki page; gera via LLM só se ausente
        self._proposal_outline = self._load_outline_from_wiki(edital_id) \
                                 or self._generate_outline()

        logger.info(
            "WritingSession %s | edital=%s | %d seções | %s/%s",
            self.session_id, edital_id, len(self._proposal_outline),
            self.backend, self.model,
        )

    # ------------------------------------------------------------------
    # Carregamento dos documentos
    # ------------------------------------------------------------------

    def _load_documents(self, edital_id: str) -> str:
        """Carrega todos os PDFs relevantes do edital e retorna texto concatenado."""
        pdf_dir = FINEP_PDFS_DIR / edital_id
        if not pdf_dir.exists():
            logger.warning("Diretório de PDFs não encontrado: %s", pdf_dir)
            return ""

        parts = []
        for pdf_path in sorted(pdf_dir.glob("*.pdf")):
            if any(kw in pdf_path.stem.lower() for kw in _SKIP_KEYWORDS):
                continue
            text = self._extract_pdf(pdf_path)
            if text.strip():
                parts.append(f"### {pdf_path.stem}\n{text}")

        result = "\n\n".join(parts)
        logger.info("Documentos carregados: %d chars de %s", len(result), edital_id)
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
        return {
            "session_id":       self.session_id,
            "edital_id":        self.edital_id,
            "section_titles":   self._proposal_outline,
            "has_documents":    bool(self._documents_text),
            "turn_count":       self._turn_count,
            "created_at":       self.created_at,
        }

    def turn(self, user_message: str, section_hint: Optional[str] = None) -> dict:
        self._turn_count += 1
        logger.info("[%s] Turno %d", self.session_id, self._turn_count)

        try:
            if self._turn_count > COMPRESS_THRESHOLD:
                self._compress_history()

            messages = self._build_messages(user_message, section_hint)
            success, response_text, error_type = self._call_llm(messages)

            if not success:
                return self._error_result(response_text, error_type)

            self._history.append({"role": "user",      "content": user_message})
            self._history.append({"role": "assistant", "content": response_text})

            return {
                "session_id":        self.session_id,
                "assistant_message": response_text,
                "turn_number":       self._turn_count,
                "success":           True,
                "error":             None,
            }

        except Exception as e:
            logger.error("[%s] Erro no turno %d: %s", self.session_id, self._turn_count, e)
            return self._error_result(str(e), "INTERNAL_ERROR")

    def get_section_starter(self, section_title: str) -> str:
        """Mensagem inicial contextualizada para uma seção da proposta."""
        messages = [
            {"role": "system", "content": WRITER_SYSTEM},
            {"role": "user",   "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
        ]
        if self._documents_text:
            messages.append({
                "role": "user",
                "content": f"DOCUMENTOS DO EDITAL:\n{self._documents_text}",
            })
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

    def get_export(self) -> str:
        parts = []
        for title in self._proposal_outline:
            content = self._doc_sections.get(title, "")
            parts.append(f"## {title}\n\n{content}" if content else f"## {title}\n\n*[A preencher]*")
        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------
    # Montagem do prompt (prefixo estático → prompt caching)
    # ------------------------------------------------------------------

    def _build_messages(self, user_message: str, section_hint: Optional[str] = None) -> list[dict]:
        """
        Estrutura do prompt (estático primeiro para maximizar cache hit):
          system   — WRITER_SYSTEM (imutável)
          user     — perfil da empresa (imutável na sessão)
          user     — documentos do edital (imutável na sessão)
          user     — contexto da biblioteca (imutável na sessão)
          ...      — histórico comprimido + janela recente
          user     — seção ativa (se houver)
          user     — mensagem atual
        """
        messages: list[dict] = [
            {"role": "system", "content": WRITER_SYSTEM},
            {"role": "user",   "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
        ]

        if self._documents_text:
            messages.append({
                "role":    "user",
                "content": f"DOCUMENTOS DO EDITAL:\n{self._documents_text}",
            })

        if self._library_context:
            messages.append({
                "role":    "user",
                "content": self._library_context,
            })

        if self._history_summary:
            messages.append({
                "role":    "user",
                "content": self._history_summary,
            })

        messages.extend(self._history)

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

    def _error_result(self, message: str, error_type: Optional[str]) -> dict:
        return {
            "session_id":        self.session_id,
            "assistant_message": f"Erro ao processar: {message}",
            "turn_number":       self._turn_count,
            "success":           False,
            "error":             message,
            "error_type":        error_type,
        }
