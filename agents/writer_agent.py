"""
Agente Redator (Radar de Editais)

Gera rascunhos de propostas tecnicas conectando
as exigencias do edital com o portfolio da empresa.
"""

import os
import logging
from typing import Optional
from datetime import datetime

import requests

from domain.user_profile import CompanyProfile

# Seções prioritárias para geração de proposta one-shot
_PROPOSAL_SECTIONS = [
    "objeto", "objetivo", "elegibilidade", "quem pode", "recurso",
    "valor", "financ", "critério", "avaliação", "cronograma",
]

# =============================================================================
# CONFIGURACAO
# =============================================================================

LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# PROMPTS
# =============================================================================

DRAFT_SYSTEM_PROMPT = """Voce e um redator tecnico especializado em propostas para editais de fomento e licitacoes no Brasil.

Sua tarefa e escrever um RASCUNHO DETALHADO de proposta tecnica em formato Markdown.
Voce deve conectar as dores/exigencias do edital com as solucoes/experiencia da empresa.

ESTRUTURA OBRIGATORIA DA PROPOSTA:

# 1. OBJETO
- Descreva o que sera entregue, alinhado ao objeto do edital.
- Conecte com a experiencia da empresa.

# 2. JUSTIFICATIVA
- Explique POR QUE a empresa e a escolha ideal.
- Cite projetos anteriores do portfolio como evidencia.
- Conecte os problemas do edital com solucoes ja executadas.

# 3. METODOLOGIA
- Descreva as etapas de execucao (fases, marcos, entregas).
- Seja especifico: cite ferramentas, frameworks, abordagens.
- Inclua cronograma macro se o edital mencionar prazo.

# 4. EQUIPE TECNICA
- Descreva perfis necessarios com base na equipe da empresa.
- Destaque qualificacoes que atendem ao edital.

# 5. RESULTADOS ESPERADOS
- Liste deliverables concretos e mensuráveis.
- Conecte com indicadores mencionados no edital.

DIRETRIZES:
- Escreva 80% da proposta tecnica, deixando marcadores [COMPLETAR] onde precisar de dados especificos.
- Use linguagem {style}: {style_desc}
- Seja propositivo: nao diga "podemos fazer", diga "faremos".
- Cite dados concretos do portfolio da empresa quando aplicavel.
- Use listas e bullet points para facilitar leitura.
- NUNCA invente dados numericos que nao estejam no perfil ou edital.
- Quando nao tiver informacao, use [COMPLETAR: descricao do que inserir]."""

STYLE_MAP = {
    "formal": "formal e tecnico, como documentos de licitacao publica",
    "consultivo": "consultivo e acessivel, como proposta de consultoria",
    "academico": "academico e cientifico, como projeto de pesquisa FAPESP/CNPq",
}


# =============================================================================
# AGENTE REDATOR
# =============================================================================

class ProposalDrafter:
    """
    Gera rascunhos de propostas tecnicas para editais.
    Conecta exigencias do edital com portfolio da empresa.
    """

    def __init__(self, llm_backend: str = None, model: str = None):
        self.backend = llm_backend or LLM_BACKEND
        if model:
            self.model = model
        elif self.backend == "ollama":
            self.model = OLLAMA_MODEL
        else:
            self.model = OPENAI_MODEL

        logger.info(f"ProposalDrafter inicializado ({self.backend}/{self.model})")

    def _call_llm(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        """Chama o LLM configurado."""
        if self.backend == "ollama":
            return self._call_ollama(messages)
        return self._call_openai(messages)

    def _call_ollama(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.5,
                        "num_predict": 4000,
                    }
                },
                timeout=300
            )
            if response.status_code != 200:
                return (False, f"Ollama retornou {response.status_code}", "API_ERROR")
            return (True, response.json()["message"]["content"], None)
        except requests.exceptions.Timeout:
            return (False, "Timeout na geracao de proposta", "TIMEOUT")
        except requests.exceptions.ConnectionError:
            return (False, "Ollama nao acessivel", "CONNECTION_ERROR")
        except Exception as e:
            logger.error(f"Erro Ollama: {e}")
            return (False, str(e), "UNKNOWN_ERROR")

    def _call_openai(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        if not OPENAI_API_KEY:
            return (False, "OPENAI_API_KEY nao configurada", "CONFIG_ERROR")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.5,
                max_tokens=4000,
            )
            return (True, response.choices[0].message.content, None)
        except ImportError:
            return (False, "Biblioteca openai nao instalada", "DEPENDENCY_ERROR")
        except Exception as e:
            logger.error(f"Erro OpenAI: {e}")
            return (False, str(e), "API_ERROR")

    def draft_proposal(
        self,
        company_profile: CompanyProfile,
        edital_id: Optional[str] = None,
        edital_context: str = "",
        style: str = "formal",
        edital_metadata: Optional[dict] = None,
    ) -> dict:
        """
        Gera rascunho de proposta tecnica.

        Args:
            company_profile: Perfil da empresa
            edital_id: ID do edital — documentos carregados pela WritingSession
            edital_context: Fallback quando edital_id não é fornecido ou section index não existe
            style: Estilo de escrita (formal, consultivo, academico)
            edital_metadata: Metadados do edital (titulo, fonte, prazo, valor)

        Returns:
            Dict com proposal_text, success, error, metadata
        """
        if not company_profile.is_complete():
            return {
                "proposal_text": "",
                "success": False,
                "error": "Perfil da empresa incompleto. Preencha ao menos nome e atividades.",
                "timestamp": datetime.now().isoformat(),
            }

        # Resolve estilo
        style_key = style if style in STYLE_MAP else "formal"
        style_desc = STYLE_MAP[style_key]

        system_prompt = DRAFT_SYSTEM_PROMPT.replace("{style}", style_key).replace("{style_desc}", style_desc)

        resolved_context = edital_context

        # Monta info do edital
        edital_section = ""
        if edital_metadata:
            parts = []
            if edital_metadata.get("title"):
                parts.append(f"Titulo: {edital_metadata['title']}")
            if edital_metadata.get("source"):
                parts.append(f"Orgao: {edital_metadata['source']}")
            if edital_metadata.get("deadline_date"):
                parts.append(f"Prazo: {edital_metadata['deadline_date']}")
            if edital_metadata.get("value_brl"):
                parts.append(f"Valor estimado: R$ {edital_metadata['value_brl']:,.2f}")
            if parts:
                edital_section = "\n".join(parts) + "\n\n"

        edital_section += resolved_context

        user_message = f"""PERFIL DA EMPRESA:
{company_profile.to_context()}

EDITAL/OPORTUNIDADE:
{edital_section}

Gere o rascunho da proposta tecnica em Markdown."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        logger.info(f"Gerando rascunho de proposta ({self.backend}/{self.model}, style={style_key})...")
        success, text, error_type = self._call_llm(messages)

        return {
            "proposal_text": text if success else "",
            "success": success,
            "error": None if success else text,
            "error_type": error_type,
            "style": style_key,
            "model": self.model,
            "timestamp": datetime.now().isoformat(),
        }


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    profile = CompanyProfile(
        nome="TechSol Inovacoes",
        descricao_atividades="Desenvolvimento de software para gestao publica",
        portfolio_projetos="Sistema de gestao escolar para prefeitura de Campinas.",
        tamanho_empresa="EPP",
        equipe_resumo="15 colaboradores. 2 engenheiros senior, 1 PM PMP.",
    )

    edital = "Contratacao de sistema web de gestao integrada para secretaria de educacao. Prazo: 12 meses."

    drafter = ProposalDrafter()
    result = drafter.draft_proposal(edital, profile, style="formal")
    if result["success"]:
        print(result["proposal_text"])
    else:
        print(f"Erro: {result['error']}")
