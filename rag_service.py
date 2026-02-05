"""
RAG Service (Radar de Editais)

Serviço de Retrieval-Augmented Generation que combina busca híbrida
com LLMs para responder perguntas sobre editais de fomento.

Autor: Pipeline Radar Editais
"""

import os
import logging
from typing import Optional
from datetime import datetime

import pandas as pd
import requests

from search_engine import HybridSearchEngine

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

# LLM Backend
LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")  # ollama | openai

# Ollama Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# RAG Configuration
DEFAULT_TOP_K = 5
MAX_CONTEXT_LENGTH = 8000  # Caracteres máximos de contexto

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# TEMPLATES DE PROMPT
# =============================================================================

SYSTEM_PROMPTS = {
    "general": """Você é um assistente especializado em editais de fomento brasileiro.
Responda de forma clara e objetiva, citando as fontes.

REGRA IMPORTANTE: Responda APENAS com base no contexto fornecido.
Se a informação não estiver no contexto, diga: "Não encontrei essa informação nos editais disponíveis."

Formato da resposta:
1. Resposta direta à pergunta
2. Detalhes relevantes
3. Fontes consultadas (título e URL)""",

    "analysis": """Você é um consultor de captação de recursos especializado em editais brasileiros.
Analise a aderência entre o perfil da empresa/projeto e o edital.

REGRA IMPORTANTE: Base sua análise APENAS no contexto fornecido.

Estruture sua resposta em:
1. **Aderência Geral**: Alta/Média/Baixa
2. **Pontos Positivos**: O que alinha com os requisitos
3. **Pontos de Atenção**: Requisitos que podem ser desafiadores
4. **Recomendação**: Submeter ou não, com justificativa
5. **Próximos Passos**: Ações sugeridas""",

    "draft": """Você é um redator técnico especializado em propostas para editais de fomento.
Crie um rascunho profissional baseado nas exigências do edital.

REGRA IMPORTANTE: Use APENAS informações do contexto fornecido.

Estruture o rascunho com:
1. **Título do Projeto** (sugestão)
2. **Resumo Executivo** (máx 200 palavras)
3. **Objetivos** (geral e específicos)
4. **Metodologia** (estrutura sugerida)
5. **Resultados Esperados**
6. **Observações**: Campos que precisam ser preenchidos pelo proponente""",

    "summary": """Você é um analista de editais de fomento.
Crie um resumo executivo claro e objetivo.

REGRA IMPORTANTE: Extraia informações APENAS do contexto fornecido.

Inclua:
1. **Objetivo do Edital**
2. **Público-alvo**
3. **Valor Disponível** (se mencionado)
4. **Prazo de Submissão**
5. **Requisitos Principais**
6. **Critérios de Avaliação** (se disponíveis)
7. **Link para mais informações**"""
}


# =============================================================================
# RAG SERVICE
# =============================================================================

class RAGService:
    """
    Serviço RAG que combina busca híbrida com geração via LLM.

    Suporta múltiplos backends (Ollama local ou OpenAI API) e
    diferentes tipos de tarefas (análise, resumo, rascunho).
    """

    def __init__(
        self,
        llm_backend: str = None,
        model: str = None,
        search_top_k: int = DEFAULT_TOP_K
    ):
        """
        Inicializa o serviço RAG.

        Args:
            llm_backend: "ollama" ou "openai" (default: variável de ambiente)
            model: Nome do modelo LLM (default: variável de ambiente)
            search_top_k: Número de documentos para contexto
        """
        logger.info("Inicializando RAGService...")

        # Configurar backend
        self.backend = llm_backend or LLM_BACKEND
        if self.backend == "ollama":
            self.model = model or OLLAMA_MODEL
        else:
            self.model = model or OPENAI_MODEL

        self.top_k = search_top_k

        # Inicializar motor de busca
        logger.info("Carregando motor de busca...")
        self.searcher = HybridSearchEngine()

        logger.info(f"RAGService inicializado (backend={self.backend}, model={self.model})")

    def _build_context(self, results: list[dict]) -> str:
        """
        Formata os resultados da busca como contexto para o LLM.

        Args:
            results: Lista de resultados da busca

        Returns:
            String formatada com o contexto
        """
        if not results:
            return "Nenhum edital encontrado para esta consulta."

        context_parts = []
        total_length = 0

        for i, doc in enumerate(results, 1):
            # Formatar deadline
            deadline = doc.get("deadline_date")
            if deadline is not None and pd.notna(deadline) and hasattr(deadline, "strftime"):
                deadline_str = deadline.strftime("%d/%m/%Y")
            else:
                deadline_str = "Não informado"

            # Formatar valor
            value = doc.get("value_brl")
            value_str = f"R$ {value:,.2f}" if value else "Não informado"

            # Truncar descrição se necessário
            description = doc.get("description", "")[:1500]
            matching_chunk = doc.get("matching_chunk", "")[:500]

            entry = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EDITAL {i}: {doc.get('title', 'Sem título')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Fonte: {doc.get('source', 'N/A')}
• Status: {doc.get('status', 'N/A')}
• Prazo: {deadline_str}
• Valor: {value_str}
• Público-alvo: {doc.get('target_audience', 'N/A')}
• Categoria: {doc.get('category', 'N/A')}
• URL: {doc.get('url', 'N/A')}

DESCRIÇÃO:
{description}

TRECHO MAIS RELEVANTE:
{matching_chunk}
"""
            # Verificar limite de contexto
            if total_length + len(entry) > MAX_CONTEXT_LENGTH:
                break

            context_parts.append(entry)
            total_length += len(entry)

        return "\n".join(context_parts)

    def _format_sources(self, results: list[dict]) -> list[dict]:
        """
        Formata as fontes para retorno simplificado.

        Args:
            results: Lista de resultados da busca

        Returns:
            Lista de fontes simplificadas
        """
        sources = []
        for doc in results:
            sources.append({
                "id": doc.get("id"),
                "title": doc.get("title"),
                "source": doc.get("source"),
                "url": doc.get("url"),
                "score": doc.get("score"),
            })
        return sources

    def _call_ollama(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        """
        Chama o LLM via Ollama local.

        Args:
            messages: Lista de mensagens no formato OpenAI

        Returns:
            Tuple de (sucesso, resposta/mensagem, tipo_erro)
            - sucesso: True se a chamada foi bem sucedida
            - resposta: Conteúdo da resposta ou mensagem de erro
            - tipo_erro: None se sucesso, ou tipo do erro (TIMEOUT, API_ERROR, etc.)
        """
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,  # Mais determinístico
                        "num_predict": 2000,
                    }
                },
                timeout=180
            )

            if response.status_code != 200:
                logger.error(f"Erro Ollama: {response.status_code} - {response.text}")
                return (False, f"Serviço Ollama retornou erro {response.status_code}", "API_ERROR")

            return (True, response.json()["message"]["content"], None)

        except requests.exceptions.Timeout:
            logger.error("Timeout ao chamar Ollama")
            return (False, "Tempo limite excedido na geração da resposta", "TIMEOUT")

        except requests.exceptions.ConnectionError:
            logger.error("Ollama não está acessível")
            return (False, "Serviço Ollama não está disponível. Verifique se está rodando.", "CONNECTION_ERROR")

        except Exception as e:
            logger.error(f"Erro ao chamar Ollama: {e}")
            return (False, f"Erro inesperado: {str(e)}", "UNKNOWN_ERROR")

    def _call_openai(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        """
        Chama o LLM via OpenAI API.

        Args:
            messages: Lista de mensagens no formato OpenAI

        Returns:
            Tuple de (sucesso, resposta/mensagem, tipo_erro)
        """
        if not OPENAI_API_KEY:
            return (False, "API Key da OpenAI não configurada", "CONFIG_ERROR")

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
            return (False, "Biblioteca 'openai' não instalada", "DEPENDENCY_ERROR")

        except Exception as e:
            logger.error(f"Erro ao chamar OpenAI: {e}")
            error_type = "RATE_LIMIT" if "rate" in str(e).lower() else "API_ERROR"
            return (False, f"Erro na API OpenAI: {str(e)}", error_type)

    def _call_llm(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        """
        Chama o LLM configurado.

        Args:
            messages: Lista de mensagens no formato OpenAI

        Returns:
            Tuple de (sucesso, resposta/mensagem, tipo_erro)
        """
        if self.backend == "ollama":
            return self._call_ollama(messages)
        else:
            return self._call_openai(messages)

    def generate(
        self,
        query: str,
        task_type: str = "general",
        filters: dict = None,
        top_k: int = None
    ) -> dict:
        """
        Gera resposta usando RAG.

        Args:
            query: Pergunta do usuário
            task_type: Tipo de tarefa:
                - "general": Resposta informativa
                - "analysis": Análise de aderência empresa/edital
                - "draft": Rascunho de proposta
                - "summary": Resumo executivo do edital
            filters: Filtros para busca (source, status, deadline_after, etc.)
            top_k: Número de documentos (override do default)

        Returns:
            {
                "answer": str,
                "sources": list[dict],
                "query": str,
                "task_type": str,
                "model": str,
                "timestamp": str
            }
        """
        logger.info(f"Gerando resposta para: '{query}' (task_type={task_type})")

        # 1. Buscar documentos relevantes
        search_top_k = top_k or self.top_k
        results = self.searcher.search(query, top_k=search_top_k, filters=filters)

        if not results:
            return {
                "answer": "Não encontrei editais relevantes para sua consulta. "
                         "Tente reformular a pergunta ou ajustar os filtros.",
                "sources": [],
                "query": query,
                "task_type": task_type,
                "model": self.model,
                "timestamp": datetime.now().isoformat()
            }

        logger.info(f"Encontrados {len(results)} documentos relevantes")

        # 2. Construir contexto
        context = self._build_context(results)

        # 3. Montar prompt
        system_prompt = SYSTEM_PROMPTS.get(task_type, SYSTEM_PROMPTS["general"])

        user_message = f"""CONTEXTO (Editais Recuperados):
{context}

PERGUNTA DO USUÁRIO:
{query}

Por favor, responda à pergunta acima utilizando APENAS as informações do contexto fornecido."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        # 4. Chamar LLM
        logger.info(f"Chamando LLM ({self.backend}/{self.model})...")
        success, answer, error_type = self._call_llm(messages)

        # 5. Retornar resultado
        response = {
            "answer": answer,
            "sources": self._format_sources(results),
            "query": query,
            "task_type": task_type,
            "model": self.model,
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "error": None if success else {
                "type": error_type,
                "message": answer,
                "recoverable": error_type in ["TIMEOUT", "RATE_LIMIT", "CONNECTION_ERROR"]
            }
        }

        # Se houve erro, limpa o answer para não confundir
        if not success:
            response["answer"] = "Não foi possível gerar a resposta. Veja o campo 'error' para detalhes."

        return response

    def chat(self, query: str, filters: dict = None) -> str:
        """
        Interface simplificada para chat.

        Args:
            query: Pergunta do usuário
            filters: Filtros opcionais

        Returns:
            Apenas a resposta em texto
        """
        result = self.generate(query, task_type="general", filters=filters)
        return result["answer"]


# =============================================================================
# CLI INTERATIVO
# =============================================================================

def print_result(result: dict):
    """Imprime o resultado formatado."""
    print("\n" + "=" * 70)
    print("RESPOSTA")
    print("=" * 70)
    print(result["answer"])

    if result["sources"]:
        print("\n" + "-" * 70)
        print("FONTES CONSULTADAS:")
        print("-" * 70)
        for i, src in enumerate(result["sources"], 1):
            print(f"{i}. [{src['source']}] {src['title'][:60]}...")
            print(f"   URL: {src['url']}")

    print("\n" + "-" * 70)
    print(f"Modelo: {result['model']} | Tipo: {result['task_type']}")
    print("=" * 70)


def main():
    """CLI interativo para testar o RAG Service."""
    print("=" * 70)
    print("RAG SERVICE - Radar de Editais")
    print("=" * 70)

    # Inicializar serviço
    service = RAGService()

    print("\nComandos:")
    print("  /analysis  - Mudar para modo análise de aderência")
    print("  /summary   - Mudar para modo resumo")
    print("  /draft     - Mudar para modo rascunho")
    print("  /general   - Voltar para modo geral (padrão)")
    print("  /filtros   - Ver filtros disponíveis")
    print("  /fonte X   - Filtrar por fonte (ex: /fonte FAPESP)")
    print("  /limpar    - Limpar filtros")
    print("  /sair      - Sair")
    print()

    current_task = "general"
    current_filters = {}

    while True:
        try:
            mode_indicator = f"[{current_task}]" if current_task != "general" else ""
            query = input(f"\n{mode_indicator} 🔍 Pergunta: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaindo...")
            break

        if not query:
            continue

        # Comandos especiais
        if query.lower() in ["/sair", "/exit", "/quit", "q"]:
            print("Saindo...")
            break

        if query.lower() == "/analysis":
            current_task = "analysis"
            print("Modo alterado para: Análise de Aderência")
            continue

        if query.lower() == "/summary":
            current_task = "summary"
            print("Modo alterado para: Resumo Executivo")
            continue

        if query.lower() == "/draft":
            current_task = "draft"
            print("Modo alterado para: Rascunho de Proposta")
            continue

        if query.lower() == "/general":
            current_task = "general"
            print("Modo alterado para: Geral")
            continue

        if query.lower() == "/filtros":
            print("\nFiltros ativos:", current_filters if current_filters else "Nenhum")
            print("\nFiltros disponíveis:")
            print("  source: PNCP, FAPESP, CNPQ, FINEP, BNDES, LEMANN, SERRAPILHEIRA")
            print("  status: ABERTA, ENCERRADA")
            continue

        if query.lower().startswith("/fonte "):
            fonte = query[7:].strip().upper()
            current_filters["source"] = [fonte]
            print(f"Filtro de fonte definido: {fonte}")
            continue

        if query.lower() == "/limpar":
            current_filters = {}
            print("Filtros limpos")
            continue

        # Gerar resposta
        print("\nProcessando...")
        result = service.generate(
            query,
            task_type=current_task,
            filters=current_filters if current_filters else None
        )
        print_result(result)


if __name__ == "__main__":
    main()
