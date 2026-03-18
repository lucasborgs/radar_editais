"""
Serviço Conversacional (Radar de Editais)

LLM alimentado por dados estruturados do matching engine.
Detecta intenção do usuário e constrói contexto adequado.

Fluxos:
- match: matching_engine → top editais → contexto LLM
- explore: filtros + dados → lista resumida → contexto LLM
- analyze: edital completo + perfil → analyst_agent
- proposal: edital + perfil + análise → writer_agent
- general: dados estruturados → contexto LLM
"""

import os
import logging
from typing import Optional
from datetime import datetime

import pandas as pd
import requests

from core.matching_engine import MatchingEngine
from domain.user_profile import CompanyProfile
from core.search_engine import HybridSearchEngine

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

MAX_CONTEXT_LENGTH = 8000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# PROMPTS
# =============================================================================

SYSTEM_PROMPTS = {
    "match": """Você é um consultor de captação de recursos especializado em editais brasileiros.
O usuário tem uma empresa e quer encontrar editais compatíveis.

Você receberá uma LISTA DE EDITAIS RANKEADOS por compatibilidade com o perfil da empresa.
Cada edital tem um score (0-100), breakdown por dimensão, e razões.

Sua tarefa:
1. Apresente os editais mais relevantes de forma clara e objetiva
2. Destaque POR QUE cada edital é compatível (ou não) com a empresa
3. Sugira prioridade de submissão baseada no score e prazo
4. Se houver riscos, mencione-os

IMPORTANTE: Mantenha a numeração original dos editais (1, 2, 3...) para que o usuário possa referenciá-los depois.
Formato: use Markdown, bullets, e seja conciso. Não repita dados que o usuário já pode ver.""",

    "explore": """Você é um assistente especializado em editais de fomento brasileiro.
Responda de forma clara e objetiva sobre os editais disponíveis.

REGRA: Responda APENAS com base no contexto fornecido.
Se a informação não estiver no contexto, diga claramente.

Formato:
1. Resposta direta
2. Detalhes relevantes
3. Fontes (título + URL)""",

    "general": """Você é um assistente do Radar de Editais, sistema de monitoramento de oportunidades de fomento no Brasil.

Você tem acesso a dados estruturados sobre editais de fomento (FAPESP, CNPq, FINEP, BNDES, etc.).
Responda de forma clara, objetiva e útil.

REGRA CRÍTICA: Responda SOMENTE com base no CONTEXTO fornecido.
NUNCA invente editais, valores, prazos ou URLs que não estejam no contexto.
Se não houver editais relevantes no contexto, diga claramente: "Não encontrei editais sobre isso na base."

Se o usuário perguntar sobre funcionalidades do sistema, explique o que é possível fazer:
- Buscar editais compatíveis com o perfil da empresa
- Analisar aderência empresa↔edital
- Gerar rascunhos de propostas técnicas
- Explorar editais por temática, fonte ou prazo""",
}

# Fontes conhecidas para detecção de intenção
_KNOWN_SOURCES = {"fapesp", "cnpq", "finep", "bndes", "arapyau", "serrapilheira", "lemann", "itau"}
_STATUS_OPEN = {"aberto", "aberta", "vigente", "ativo", "abertas", "disponível", "disponíveis", "disponivel"}
_STATUS_CLOSED = {"encerrado", "encerrada", "fechado", "expirado", "vencido"}


# =============================================================================
# SERVIÇO CONVERSACIONAL
# =============================================================================

class RAGService:
    """
    Serviço conversacional alimentado por dados estruturados.
    Usa MatchingEngine para ranking e LLM para interação natural.
    """

    def __init__(
        self,
        llm_backend: str = None,
        model: str = None,
        profile: CompanyProfile = None,
    ):
        self.backend = llm_backend or LLM_BACKEND
        if model:
            self.model = model
        elif self.backend == "ollama":
            self.model = OLLAMA_MODEL
        else:
            self.model = OPENAI_MODEL

        self.profile = profile
        self.engine = MatchingEngine()
        self._hybrid_engine = None  # lazy init: instanciado na primeira busca general

        logger.info(f"RAGService inicializado (backend={self.backend}, model={self.model})")

    def set_profile(self, profile: CompanyProfile):
        """Atualiza o perfil da empresa."""
        self.profile = profile

    # ─── LLM CALLS ──────────────────────────────────────────────────────

    def _call_ollama(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 2000}
                },
                timeout=180
            )
            if response.status_code != 200:
                return (False, f"Ollama retornou {response.status_code}", "API_ERROR")
            return (True, response.json()["message"]["content"], None)
        except requests.exceptions.Timeout:
            return (False, "Timeout na geração da resposta", "TIMEOUT")
        except requests.exceptions.ConnectionError:
            return (False, "Ollama não acessível. Verifique se está rodando.", "CONNECTION_ERROR")
        except Exception as e:
            return (False, str(e), "UNKNOWN_ERROR")

    def _call_openai(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        if not OPENAI_API_KEY:
            return (False, "OPENAI_API_KEY não configurada", "CONFIG_ERROR")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=2000,
            )
            return (True, response.choices[0].message.content, None)
        except ImportError:
            return (False, "Biblioteca openai não instalada", "DEPENDENCY_ERROR")
        except Exception as e:
            error_type = "RATE_LIMIT" if "rate" in str(e).lower() else "API_ERROR"
            return (False, str(e), error_type)

    def _call_llm(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        if self.backend == "ollama":
            return self._call_ollama(messages)
        return self._call_openai(messages)

    # ─── INTENT DETECTION ─────────────────────────────────────────────────

    def _detect_intent(self, query: str) -> dict:
        """Extrai fonte e status mencionados na query via keyword matching."""
        q = query.lower()
        source = next((s.upper() for s in _KNOWN_SOURCES if s in q), None)
        status = None
        if any(w in q for w in _STATUS_OPEN):
            status = "ABERTA"
        elif any(w in q for w in _STATUS_CLOSED):
            status = "ENCERRADA"
        return {"source": source, "status": status}

    def _get_hybrid_engine(self) -> HybridSearchEngine:
        """Inicializa HybridSearchEngine sob demanda (lazy init)."""
        if self._hybrid_engine is None:
            self._hybrid_engine = HybridSearchEngine()
        return self._hybrid_engine

    def _search_context(self, query: str, source: str = None, status: str = None, top_k: int = 10) -> str:
        """Busca semântica + lexical via HybridSearchEngine (ChromaDB + BM25 + RRF)."""
        filters = {}
        if source:
            filters["source"] = source
        if status:
            filters["status"] = status

        engine = self._get_hybrid_engine()
        results = engine.search(query, top_k=top_k, filters=filters or None)

        if not results:
            return "Nenhum edital encontrado para essa busca na base."

        parts = [f"Editais encontrados na base: {len(results)}\n"]
        for r in results:
            deadline = r.get("deadline_date")
            if deadline is not None and not (isinstance(deadline, float) and pd.isna(deadline)):
                deadline_str = str(deadline)[:10]
            else:
                deadline_str = "N/I"

            parts.append(
                f"- [{r['source']}] {str(r['title'])[:80]}\n"
                f"  Status: {r.get('status', '')} | Prazo: {deadline_str} | Score: {r['score']:.3f}\n"
                f"  URL: {r.get('url', '')}\n"
                f"  Trecho relevante: {r.get('matching_chunk', '')[:300]}"
            )

        return "\n".join(parts)

    # ─── CONTEXT BUILDERS ────────────────────────────────────────────────

    def _build_match_context(self, matches: list[dict]) -> str:
        """Constrói contexto a partir dos resultados do matching engine."""
        if not matches:
            return "Nenhum edital compatível encontrado."

        parts = []
        for i, m in enumerate(matches[:10], 1):
            deadline = m.get("deadline_date", "Não informado") or "Não informado"
            breakdown_str = " | ".join(f"{k}:{v}" for k, v in m["breakdown"].items() if v > 0)

            entry = f"""
--- EDITAL {i} [{m['total_score']:.0f}pts | {m['recommendation']}] ---
Título: {m['title']}
Fonte: {m['source']} | Categoria: {m['category']}
Prazo: {deadline} | Status: {m['status']}
URL: {m['url']}
Score breakdown: {breakdown_str}
Temáticas: {', '.join(m.get('themes', []))}
Resumo: {m['description_preview']}
"""
            if len("\n".join(parts) + entry) > MAX_CONTEXT_LENGTH:
                break
            parts.append(entry)

        return "\n".join(parts)

    def _build_edital_context(self, edital: dict) -> str:
        """Constrói contexto completo de um edital."""
        desc = str(edital.get("description", ""))[:4000]
        themes = edital.get("themes", [])
        if isinstance(themes, list):
            themes_str = ", ".join(themes)
        else:
            themes_str = str(themes) if themes else ""

        return f"""
Título: {edital.get('title', '')}
Fonte: {edital.get('source', '')}
Status: {edital.get('status', '')}
Categoria: {edital.get('category', '')}
URL: {edital.get('url', '')}
Temáticas: {themes_str}

Descrição completa:
{desc}
"""

    def _build_explore_context(self, source_filter: list[str] = None, status_filter: str = "ABERTA") -> str:
        """Constrói contexto de exploração de editais."""
        df = self.engine.df
        if df is None or df.empty:
            return "Nenhum edital disponível."

        if status_filter:
            df = df[df["status"] == status_filter]
        if source_filter:
            df = df[df["source"].isin(source_filter)]

        parts = [f"Total de editais: {len(df)}\n"]

        for _, row in df.head(15).iterrows():
            deadline = row.get("deadline_date")
            deadline_str = ""
            if deadline is not None and not (isinstance(deadline, float) and pd.isna(deadline)):
                deadline_str = str(deadline)[:10] if hasattr(deadline, "isoformat") else str(deadline)

            themes = row.get("themes", [])
            themes_str = ", ".join(themes) if isinstance(themes, list) else ""

            parts.append(
                f"- [{row.get('source', '')}] {row.get('title', '')[:80]} | "
                f"Prazo: {deadline_str or 'N/I'} | Temas: {themes_str[:60]}"
            )

        return "\n".join(parts)

    # ─── GENERATE ────────────────────────────────────────────────────────

    def generate(
        self,
        query: str,
        task_type: str = "general",
        filters: dict = None,
        top_k: int = 10,
        history: list = None,
    ) -> dict:
        """
        Gera resposta conversacional.

        Args:
            query: Pergunta do usuário
            task_type: match | explore | general
            filters: Filtros opcionais (source, status)
            top_k: Quantidade de resultados
            history: Histórico de mensagens anteriores [{"role": "user"|"assistant", "content": str}]

        Returns:
            Dict com answer, sources, matches, metadata
        """
        logger.info(f"Gerando resposta: '{query[:60]}' (task={task_type})")

        source_filter = None
        status_filter = None
        if filters:
            source_filter = filters.get("source")
            status_filter = filters.get("status")

        # Determinar fluxo
        matches = []
        context = ""
        system_key = task_type if task_type in SYSTEM_PROMPTS else "general"

        if task_type == "match" and self.profile and self.profile.is_complete():
            # Matching: empresa → editais rankeados
            matches = self.engine.match(
                self.profile,
                top_k=top_k,
                source_filter=source_filter,
                status_filter=status_filter,
            )
            context = self._build_match_context(matches)
            profile_ctx = f"\nPERFIL DA EMPRESA:\n{self.profile.to_context()}\n"
            context = profile_ctx + context

        elif task_type == "explore":
            # Explorar: lista de editais
            context = self._build_explore_context(
                source_filter=source_filter,
                status_filter=status_filter,
            )

        else:
            # General: detecção de intenção + busca dinâmica (sem alucinação)
            intent = self._detect_intent(query)
            src = intent.get("source") or source_filter
            sts = intent.get("status") or status_filter

            stats = self.engine.get_stats()
            header = f"Base total: {stats.get('total', 0)} editais de {len(stats.get('by_source', {}))} fontes ({', '.join(stats.get('by_source', {}).keys())}).\n\n"
            context = header + self._search_context(query, source=src, status=sts, top_k=top_k)

        # Montar prompt com histórico
        system_prompt = SYSTEM_PROMPTS[system_key]
        user_message = f"""CONTEXTO:
{context}

PERGUNTA:
{query}"""

        messages = [{"role": "system", "content": system_prompt}]
        # Injetar histórico (últimas 6 mensagens = 3 pares) para memória de conversa
        if history:
            for h in history[-6:]:
                if h.get("role") in ("user", "assistant"):
                    messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        # Chamar LLM
        success, answer, error_type = self._call_llm(messages)

        response = {
            "answer": answer if success else "Não foi possível gerar a resposta.",
            "matches": matches,
            "sources": [
                {"id": m["edital_id"], "title": m["title"], "source": m["source"],
                 "url": m["url"], "score": m["total_score"]}
                for m in matches
            ],
            "query": query,
            "task_type": task_type,
            "model": self.model,
            "success": success,
            "error": None if success else {"type": error_type, "message": answer},
            "timestamp": datetime.now().isoformat(),
        }

        return response

    def chat(self, query: str, filters: dict = None) -> str:
        """Interface simplificada."""
        result = self.generate(query, task_type="general", filters=filters)
        return result["answer"]


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    from user_profile import CompanyProfile

    profile = CompanyProfile(
        nome="TechSol Inovações",
        descricao_atividades="Desenvolvimento de software e IA para gestão pública",
        tamanho_empresa="EPP",
        localizacao="São Paulo/SP",
    )

    service = RAGService(profile=profile)

    print("Radar de Editais - Chat")
    print("Comandos: /match, /explore, /general, /sair\n")

    task = "general"
    while True:
        try:
            q = input(f"[{task}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ["/sair", "/exit"]:
            break
        if q.startswith("/"):
            task = q[1:]
            print(f"Modo: {task}")
            continue

        result = service.generate(q, task_type=task)
        print(f"\n{result['answer']}\n")
