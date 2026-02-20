"""
Agente Analista (Radar de Editais)

Consultor digital que analisa aderencia empresa/edital,
identifica riscos de desclassificacao e documentos exigidos.
"""

import os
import json
import logging
from typing import Optional
from datetime import datetime

import requests

from user_profile import CompanyProfile

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

ADHERENCE_SYSTEM_PROMPT = """Voce e um consultor especialista em licitacoes e editais de fomento no Brasil.
Sua tarefa e comparar o PERFIL DA EMPRESA com os REQUISITOS DO EDITAL usando uma analise em 3 etapas.

Voce DEVE responder EXCLUSIVAMENTE em JSON valido, sem markdown, sem explicacoes fora do JSON.

O JSON deve ter exatamente esta estrutura:
{
  "reasoning": {
    "step1_requirements": ["<requisito extraido do edital 1>", "<requisito 2>", "..."],
    "step2_capabilities": ["<capacidade da empresa que atende requisito 1>", "..."],
    "step3_gaps": ["<lacuna/gap identificado 1>", "..."]
  },
  "score": <inteiro 0-100>,
  "summary": "<resumo executivo da oportunidade em 2-3 frases>",
  "positives": ["<ponto positivo 1>", "<ponto positivo 2>"],
  "risks": ["<risco 1>", "<risco 2>"],
  "missing_docs": ["<documento exigido 1>", "<documento exigido 2>"],
  "recommendation": "<SUBMETER|AVALIAR_COM_CAUTELA|NAO_SUBMETER>"
}

INSTRUCOES PARA CADA ETAPA:

ETAPA 1 - EXTRACAO DE REQUISITOS (step1_requirements):
Liste TODOS os requisitos identificados no edital: CNAEs, porte, capital social,
certificacoes, equipe tecnica, documentos de habilitacao, restricoes geograficas, prazos.

ETAPA 2 - MAPEAMENTO DE CAPACIDADES (step2_capabilities):
Para cada requisito da Etapa 1, verifique se o perfil da empresa atende.
Liste apenas os requisitos que a empresa ATENDE com evidencia do perfil.

ETAPA 3 - GAPS (step3_gaps):
Liste requisitos da Etapa 1 que NAO foram cobertos na Etapa 2.
Inclua requisitos parcialmente atendidos ou que nao puderam ser verificados.

REGRAS DE SCORING (baseado nas 3 etapas):
- 80-100: Etapa 3 tem 0-1 gaps menores. Alta aderencia.
- 50-79: Etapa 3 tem 2-3 gaps moderados. Media aderencia.
- 20-49: Etapa 3 tem gaps criticos. Baixa aderencia.
- 0-19: Etapa 3 mostra falha em requisitos fundamentais.

- Se o perfil da empresa estiver incompleto, penalize o score e mencione nos riscos.
- Seja conservador: na duvida, sinalize como risco.

IMPORTANTE: Responda APENAS com o JSON. Preencha o reasoning ANTES de calcular o score."""


RISK_CATEGORIES = {
    "capital_social": "Exigencia de capital social",
    "porte": "Restricao de porte empresarial",
    "cnae": "CNAE incompativel",
    "equipe": "Exigencia de equipe tecnica qualificada",
    "experiencia": "Atestado de capacidade tecnica",
    "certificacao": "Certificacao ou acreditacao obrigatoria",
    "localizacao": "Restricao geografica",
    "prazo": "Prazo de submissao apertado",
    "documentacao": "Documentacao complexa exigida",
}


# =============================================================================
# AGENTE ANALISTA
# =============================================================================

class AdherenceAnalyzer:
    """
    Analisa aderencia entre perfil da empresa e editais.
    Identifica riscos de desclassificacao e documentos exigidos.
    """

    def __init__(self, llm_backend: str = None, model: str = None):
        self.backend = llm_backend or LLM_BACKEND
        if model:
            self.model = model
        elif self.backend == "ollama":
            self.model = OLLAMA_MODEL
        else:
            self.model = OPENAI_MODEL

        logger.info(f"AdherenceAnalyzer inicializado ({self.backend}/{self.model})")

    def _call_llm(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        """Chama o LLM configurado."""
        if self.backend == "ollama":
            return self._call_ollama(messages)
        return self._call_openai(messages)

    def _call_ollama(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        """Chama Ollama."""
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 2000,
                    },
                    "format": "json"
                },
                timeout=180
            )
            if response.status_code != 200:
                return (False, f"Ollama retornou {response.status_code}", "API_ERROR")
            return (True, response.json()["message"]["content"], None)

        except requests.exceptions.Timeout:
            return (False, "Timeout na chamada Ollama", "TIMEOUT")
        except requests.exceptions.ConnectionError:
            return (False, "Ollama nao acessivel", "CONNECTION_ERROR")
        except Exception as e:
            logger.error(f"Erro Ollama: {e}")
            return (False, str(e), "UNKNOWN_ERROR")

    def _call_openai(self, messages: list[dict]) -> tuple[bool, str, Optional[str]]:
        """Chama OpenAI."""
        if not OPENAI_API_KEY:
            return (False, "OPENAI_API_KEY nao configurada", "CONFIG_ERROR")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=2000,
                response_format={"type": "json_object"}
            )
            return (True, response.choices[0].message.content, None)
        except ImportError:
            return (False, "Biblioteca openai nao instalada", "DEPENDENCY_ERROR")
        except Exception as e:
            logger.error(f"Erro OpenAI: {e}")
            return (False, str(e), "API_ERROR")

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """Extrai JSON da resposta do LLM com tolerancia a ruido."""
        text = text.strip()

        # Tenta parse direto
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Tenta extrair JSON de bloco markdown
        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Tenta encontrar o primeiro { ... } valido
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        return None

    def _default_result(self, error_msg: str) -> dict:
        """Retorna resultado padrao em caso de erro."""
        return {
            "score": 0,
            "summary": f"Nao foi possivel analisar: {error_msg}",
            "positives": [],
            "risks": ["Analise automatica indisponivel. Avalie manualmente."],
            "missing_docs": [],
            "recommendation": "AVALIAR_COM_CAUTELA",
            "success": False,
            "error": error_msg,
        }

    def calculate_score(
        self,
        edital_text: str,
        company_profile: CompanyProfile,
        edital_metadata: Optional[dict] = None
    ) -> dict:
        """
        Calcula score de aderencia empresa vs edital.

        Args:
            edital_text: Texto completo ou chunks relevantes do edital
            company_profile: Perfil da empresa
            edital_metadata: Metadados do edital (titulo, fonte, prazo, etc.)

        Returns:
            Dict com score, summary, risks, missing_docs, recommendation
        """
        if not company_profile.is_complete():
            return self._default_result("Perfil da empresa incompleto. Preencha ao menos nome e atividades.")

        if not edital_text or len(edital_text.strip()) < 50:
            return self._default_result("Texto do edital insuficiente para analise.")

        # Monta contexto do edital
        edital_context = ""
        if edital_metadata:
            meta_parts = []
            if edital_metadata.get("title"):
                meta_parts.append(f"Titulo: {edital_metadata['title']}")
            if edital_metadata.get("source"):
                meta_parts.append(f"Fonte: {edital_metadata['source']}")
            if edital_metadata.get("deadline_date"):
                meta_parts.append(f"Prazo: {edital_metadata['deadline_date']}")
            if edital_metadata.get("value_brl"):
                meta_parts.append(f"Valor: R$ {edital_metadata['value_brl']:,.2f}")
            if edital_metadata.get("category"):
                meta_parts.append(f"Categoria: {edital_metadata['category']}")
            if meta_parts:
                edital_context = "\n".join(meta_parts) + "\n\n"

        edital_context += edital_text[:6000]

        # Monta prompt
        user_message = f"""PERFIL DA EMPRESA:
{company_profile.to_context()}

EDITAL/OPORTUNIDADE:
{edital_context}

Analise a aderencia entre a empresa e este edital. Responda em JSON."""

        messages = [
            {"role": "system", "content": ADHERENCE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]

        logger.info(f"Calculando score de aderencia ({self.backend}/{self.model})...")
        success, response_text, error_type = self._call_llm(messages)

        if not success:
            return self._default_result(response_text)

        # Parse JSON
        result = self._parse_json_response(response_text)
        if not result:
            logger.warning(f"Resposta LLM nao e JSON valido: {response_text[:200]}")
            return self._default_result("Resposta do modelo nao e JSON valido")

        # Valida e normaliza campos
        score = max(0, min(100, int(result.get("score", 0))))

        return {
            "score": score,
            "summary": result.get("summary", ""),
            "positives": result.get("positives", []),
            "risks": result.get("risks", []),
            "missing_docs": result.get("missing_docs", []),
            "recommendation": result.get("recommendation", "AVALIAR_COM_CAUTELA"),
            "reasoning": result.get("reasoning", {}),
            "success": True,
            "error": None,
            "model": self.model,
            "timestamp": datetime.now().isoformat(),
        }


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    profile = CompanyProfile(
        nome="TechSol Inovacoes",
        cnpj="12.345.678/0001-90",
        descricao_atividades="Desenvolvimento de software para gestao publica, sistemas web e apps mobile",
        cnaes=["6201-5/01", "6202-3/00"],
        portfolio_projetos="Sistema de gestao escolar para prefeitura de Campinas. App de fiscalizacao ambiental para IBAMA.",
        tamanho_empresa="EPP",
        localizacao="Sao Paulo/SP",
        capital_social=250000.0,
        equipe_resumo="15 colaboradores. 2 engenheiros de software senior, 1 gerente de projetos PMP.",
    )

    edital_text = """
    Contratacao de empresa para desenvolvimento de sistema de gestao integrada.
    Requisitos: Empresa com CNAE 6201 ou 6202. Capital social minimo R$ 100.000,00.
    Equipe minima: 1 gerente de projetos certificado PMP, 2 desenvolvedores senior.
    Documentacao: Balanco Patrimonial, Certidao FGTS, Certidao Negativa Federal.
    Atestado de capacidade tecnica comprovando execucao de projeto similar.
    """

    analyzer = AdherenceAnalyzer()
    result = analyzer.calculate_score(edital_text, profile)
    print(json.dumps(result, indent=2, ensure_ascii=False))
