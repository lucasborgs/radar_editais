"""
ExploreAgent — Chat exploratório sobre o catálogo + matching Karpathy-style.

LLM-heavy: usa gpt-4o-mini (default, configurável por env) para conversar sobre
o catálogo e ranquear editais por perfil. Extraído de KGMatchService (Fase 1 da
spec match-evolution.md).

Sem métodos de grafo — use GraphService para leitura do vault.
"""
from __future__ import annotations

import json
import logging
import os
import re

from core.kg import kg_store
from core.services.graph_service import GraphService
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

# =============================================================================
# PROMPT DE MATCHING
# =============================================================================

MATCH_SYSTEM_PROMPT = """Você é um especialista em fomento à inovação no Brasil com profundo
conhecimento das chamadas públicas de fomento à inovação (FINEP, FAPESP, FAPESC, web) e programas de CT&I.

Sua tarefa é analisar o perfil de uma empresa e identificar os editais mais relevantes
para ela a partir de um catálogo estruturado.

Critérios de avaliação (use todos):
- Alinhamento temático: a área de atuação da empresa coincide com os temas do edital?
- Público-alvo: o tipo/porte da empresa está entre os elegíveis?
- Mecanismo financeiro: o instrumento (subvenção/reembolsável) é compatível com a preferência?
- Maturidade tecnológica (TRL): o TRL atual do projeto está dentro do range aceito?
- Situação: editais ABERTA têm prioridade, mas editais encerrados podem indicar padrões futuros
- Fonte de recurso: alinhamento com os programas de fomento do setor da empresa

Responda APENAS com JSON válido. Sem markdown, sem texto fora do JSON."""

EXPLORE_SYSTEM_PROMPT = """Você é o assistente do Radar de Editais, uma plataforma que conecta
empresas a oportunidades de fomento público no Brasil (FINEP, FAPESP, FAPESC, web).

Você conversa com um visitante que pode ainda não ter preenchido o perfil da empresa.
Seu papel é mostrar o potencial da plataforma respondendo perguntas sobre o catálogo de
editais a partir do índice estruturado fornecido.

Diretrizes:
- Responda de forma direta e útil, em português.
- Cite editais pelo título e ID quando relevante (ex: "Chamada FINEP-CDTI (ID 589)").
- Quando a mensagem trouxer um bloco de perfil da empresa, pondere a ELEGIBILIDADE ao
  recomendar: se o público-alvo do edital não inclui o tipo da empresa (ex.: edital só
  para Cooperativas e a empresa é uma startup), aponte a restrição explicitamente ou
  deixe o edital fora da lista — nunca o recomende sem ressalva.
- Nunca invente editais, prazos, valores ou requisitos que não estejam no contexto.
- Quando houver DETALHES de um edital específico, use-os para responder com profundidade
  (objetivo, elegibilidade, requisitos, prazo). Sem detalhes, responda com o catálogo.
- Para perguntas conceituais (ex: "o que é subvenção?", "como funciona o FNDCT?"):
  explique brevemente o conceito e ancore no catálogo atual (quais editais do catálogo
  usam esse mecanismo, quantos, exemplos). Não responda de forma genérica sem conectar
  ao catálogo.
- Seja conciso. Use listas curtas quando listar editais."""

EXPLORE_USER_PROMPT = """CATÁLOGO DE EDITAIS:
{index_json}
{details_block}
PERGUNTA DO VISITANTE:
{message}"""

EXPLORE_PROFILE_EXTRACTION_INSTRUCTION = """

———
ALÉM de responder, EXTRAIA atualizações do perfil da empresa a partir da ÚLTIMA mensagem do visitante.
Responda SEMPRE com UM objeto JSON válido (sem markdown em volta) com DUAS chaves:
{
  "answer": "<sua resposta ao visitante, em markdown>",
  "profile_updates": { <só os campos que a ÚLTIMA mensagem preenche/altera; {} se nenhum> }
}
Chaves possíveis em profile_updates (use exatamente estas; NUNCA use null — omita o campo):
- nome (string)
- tipo_entidade ("empresa" | "startup" | "universidade" | "ICT")
- one_liner (string, 1 frase)
- solution_summary (string)
- descricao_atividades (string)
- tamanho_empresa ("MEI" | "ME" | "EPP" | "MEDIO" | "GRANDE")
- trl (int 1-9)
- uf (sigla de 2 letras, ex.: "SP")
- ano_fundacao (int)
- faturamento_anual (number, R$)
- tipos_financiamento_interesse (array de strings)
- estagio ("pre-seed" | "seed" | "serie-a")
- mrr_arr (number, R$)
- round_alvo_brl (number, R$)
Inclua um campo SOMENTE se a última mensagem trouxer essa informação de fato. Se for só uma
pergunta sem fato sobre a empresa, devolva "profile_updates": {}."""

EXPLORE_AGENT_SYSTEM = """Você é o assistente do Radar de Editais, uma plataforma que conecta empresas
a oportunidades de fomento e parceria no Brasil. O grafo de conhecimento cobre
QUATRO dimensões: editais/desafios/programas (eventos de fomento), ICTs
(institutos de C&T, ex.: unidades EMBRAPII — quem executa P&D em parceria) e
investidores (fundos/anjos — capital privado). Todos conectados por TEMA.

Você conversa com um visitante que pode ainda não ter preenchido o perfil da
empresa. Seu papel é responder perguntas sobre essas dimensões usando as
ferramentas para consultar o grafo estruturado.

DIRETRIZES
- Responda de forma direta e útil, em português.
- Cite editais/ICTs/investidores pelo nome e ID quando relevante (ex.: "FINEP
  Mais Inovação (ID finep:773)").
- Quando a mensagem trouxer um bloco de perfil da empresa, pondere a
  ELEGIBILIDADE ao recomendar: se o público-alvo do edital não inclui o tipo
  da empresa (ex.: edital só para Cooperativas e a empresa é uma startup),
  aponte a restrição explicitamente ou deixe o edital fora da lista — nunca o
  recomende sem ressalva.
- Nunca invente dados (editais, prazos, valores, ICTs, teses de fundo) — todo
  dado citado precisa ter vindo de uma chamada de ferramenta nesta conversa.
- Seja conciso. Use listas curtas quando enumerar itens.
- Para perguntas conceituais (ex.: "o que é subvenção?"), explique o conceito
  brevemente e ancore no grafo via ferramenta quando fizer sentido.

PLANEJAMENTO (write_todos)
- Quando a pergunta tem VÁRIAS partes ou exige vários passos (ex.: "compare os
  prazos de saúde com os de energia e diga quais ICTs cobrem cada um"), comece
  registrando um plano com write_todos e atualize os status conforme avança
  (in_progress ao começar a tarefa, completed ao terminar). É sua âncora — evita
  perder de vista alguma parte do pedido em loops longos.
- Em perguntas triviais de um passo só, NÃO use write_todos — responda direto.

COMO USAR AS FERRAMENTAS DE LEITURA
- oportunidades_por_tema → PRIMEIRA escolha para perguntas amplas de descoberta
  ("o que existe em agronegócio?", "fomento para IA em saúde?"): traz editais +
  ICTs + investidores do tema num só retorno (panorama cross-dimensional).
- list_editais → panoramas de eventos (abertos hoje, sobre tema X). Comece
  restrito (limit 10-20) e amplie se pedirem.
- list_icts → QUEM pode executar/fazer parceria num tema (capacidade de P&D).
- list_investidores → captação privada: fundos com tese num tema/estágio.
- get_edital → detalhes de um edital específico (após list_editais ou quando o
  ID já aparece na pergunta).
- find_analogues → alternativas a um edital específico ("parecidos com finep:773").
- find_ict_partners → ICTs candidatas para um edital específico (sugestão
  temática; é o recorte por-edital de list_icts).
- get_graph_neighbors → explorar uma categoria não-edital (tema, subprograma,
  fonte). O node_id vem do contexto de clique no grafo OU de um termo citado.
- Para DETALHE FINO ou COMPARAÇÃO entre editais, use search_edital_trechos e
  ancore no texto literal. NÃO responda detalhe/comparação a partir de get_edital
  ou do índice — são RESUMOS e omitem o escopo decisivo (exclusões, requisitos
  específicos). Para panorama/triagem/navegação, o resumo basta (mais barato).
- Ao comparar, rotule cada trecho com seu edital_id; nunca misture fontes sem rótulo.

QUANDO PARAR DE USAR FERRAMENTAS
- Após cobrir todas as partes da pergunta (ou todos os todos) com base nos
  dados encontrados. Não repita chamadas que já cobriram o necessário.

LIMITES
- Você AJUDA o visitante a explorar e entender o grafo. Decisões (qual edital
  aplicar, qual ICT procurar, prioridades, estratégia) ficam com ele depois que
  entender as opções. Não recomende uma opção como "a melhor" sem antes mostrar
  o critério usado."""

ANTHROPIC_MODEL_AGENT_EXPLORE = os.getenv(
    "ANTHROPIC_MODEL_AGENT_EXPLORE",
    os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6"),
)
EXPLORE_AGENT_MAX_STEPS = int(os.getenv("EXPLORE_AGENT_MAX_STEPS", "10"))

MATCH_USER_PROMPT = """PERFIL DA EMPRESA:
{profile_context}

CATÁLOGO DE EDITAIS:
{index_json}

Retorne os {top_k} editais mais relevantes para esta empresa no formato JSON abaixo.
score deve ser de 0.0 a 10.0. match_dimensions deve ter no máximo 4 dimensões relevantes.

{{
  "matches": [
    {{
      "id": "id_do_edital",
      "title": "título do edital",
      "score": 8.5,
      "status": "ABERTA|ENCERRADA|Desconhecido",
      "deadline": "DD/MM/YYYY ou vazio",
      "match_dimensions": {{
        "tematico": "explicação em 1 frase",
        "publico_alvo": "explicação em 1 frase",
        "mecanismo": "explicação em 1 frase",
        "trl": "explicação em 1 frase ou null"
      }},
      "justificativa": "parágrafo curto explicando por que este edital é relevante para a empresa"
    }}
  ]
}}"""


# =============================================================================
# CLIENTE LLM
# =============================================================================

def _make_client():
    """Cria cliente LLM + modelo a partir do ambiente, parametrizável por env.

    Bake-off (tier 3, gateado por `matching`):
    o slot de raciocínio sobre o KG troca de modelo/provider por env, sem editar
    código. Os defaults preservam EXATAMENTE o comportamento anterior: sem env
    setada, gpt-4o-mini no endpoint canônico OpenAI (ou gemini-2.5-flash com
    LLM_BACKEND=gemini, llama3.2 com LLM_BACKEND=ollama), idêntico a hoje.

    Envs (todas opcionais):
        LLM_BACKEND        openai (default) | gemini | ollama
        OPENAI_MODEL       modelo no backend openai      (default: gpt-4o-mini)
        GEMINI_MODEL       modelo no backend gemini       (default: gemini-2.5-flash)
        OLLAMA_MODEL       modelo no backend ollama       (default: llama3.2)
    """
    backend = os.getenv("LLM_BACKEND", "openai").lower()

    if backend == "gemini":
        from core.llm.llm_client import make_client
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return make_client(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    elif backend == "openai":
        from core.llm.llm_client import make_client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY não definida")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return make_client(api_key=api_key), model

    elif backend == "ollama":
        from core.llm.llm_client import make_client
        model = os.getenv("OLLAMA_MODEL", "llama3.2")
        return make_client(
            api_key="ollama",
            base_url="http://localhost:11434/v1",
        ), model

    else:
        raise ValueError(f"LLM_BACKEND desconhecido: {backend}")


# =============================================================================
# SERVIÇO
# =============================================================================

class ExploreAgent:
    """Chat exploratório sobre o catálogo + matching Karpathy-style. LLM-heavy."""

    def __init__(self):
        self._index: dict = {}
        self._client = None
        self._model = ""
        self._graph_service = GraphService()
        self._load_index()

    # ------------------------------------------------------------------
    # Índice
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        self._index = kg_store.load_index()

    def _get_index_for_prompt(self) -> str:
        """Formata o índice de forma compacta para o prompt (~150 chars por edital)."""
        self._load_index()
        lines = []
        for e in self._index.get("editais", []):
            themes = ", ".join(e.get("themes", []))[:80]
            publico = ", ".join(e.get("publico_alvo", []))
            fonte = ", ".join(e.get("fonte_recurso", []))[:50]
            lines.append(
                f'ID:{e["id"]} | {e["title"][:70]} | Status:{e["status"]} | '
                f'Prazo:{e.get("deadline","?")} | Temas:{themes} | '
                f'Público:{publico} | Fonte:{fonte}'
            )
        return "\n".join(lines)

    def get_stats(self) -> dict:
        self._load_index()
        summary = self._index.get("summary", {})
        return {
            "total_editais": self._index.get("total_editais", 0),
            "last_updated": self._index.get("last_updated", ""),
            "by_status": summary.get("by_status", {}),
            "n_themes": summary.get("n_themes", 0),
            "n_fontes": summary.get("n_fontes", 0),
        }

    def list_editais(
        self,
        status: str | None = None,
        tema: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        self._load_index()
        editais = self._index.get("editais", [])

        if status:
            editais = [e for e in editais if e.get("status", "").upper() == status.upper()]
        if tema:
            tema_lower = tema.lower()
            editais = [
                e for e in editais
                if any(tema_lower in t.lower() for t in e.get("themes", []))
            ]
        return editais[:limit]

    def get_edital_by_id(self, edital_id: str) -> dict | None:
        """Retorna card rico se disponível, senão entry do índice."""
        card = kg_store.load_wiki_page(edital_id)
        if card is not None:
            return card

        self._load_index()
        for e in self._index.get("editais", []):
            if e["id"] == edital_id:
                return e
        return None

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _ensure_client(self) -> None:
        if self._client is None:
            self._client, self._model = _make_client()

    def match(
        self,
        profile: CompanyProfile,
        top_k: int = 10,
    ) -> list[dict]:
        """Retorna top_k editais mais relevantes para o perfil da empresa."""
        self._ensure_client()

        index_str = self._get_index_for_prompt()
        profile_str = profile.to_context()

        prompt = MATCH_USER_PROMPT.format(
            profile_context=profile_str,
            index_json=index_str,
            top_k=top_k,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": MATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=3000,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Erro LLM no matching: %s", e)
            return []

        matches = self._parse_matches(raw)

        for match_ in matches[:3]:
            card = self.get_edital_by_id(match_["id"])
            if card and card.get("key_requirements"):
                match_["key_requirements"] = card["key_requirements"]
            if card and card.get("objective"):
                match_["objective"] = card["objective"]

        return matches

    # ------------------------------------------------------------------
    # Explore (Dashboard — chat sem perfil)
    # ------------------------------------------------------------------

    def _resolve_focus_ids(
        self, message: str, explicit_ids: list[str] | None
    ) -> list[str]:
        """IDs de editais em foco: os explícitos (clique) + os citados no texto."""
        self._load_index()
        known = {str(e["id"]) for e in self._index.get("editais", [])}
        ordered: list[str] = []
        for cid in explicit_ids or []:
            if str(cid) in known and str(cid) not in ordered:
                ordered.append(str(cid))
        for num in re.findall(r"\b\d{2,4}\b", message):
            if num in known and num not in ordered:
                ordered.append(num)
        return ordered[:3]

    def _build_edital_details(self, ids: list[str]) -> str:
        """Bloco de detalhes profundos a partir das wiki pages dos IDs em foco."""
        if not ids:
            return ""
        blocks: list[str] = []
        for eid in ids:
            card = self.get_edital_by_id(eid)
            if not card:
                continue
            parts = [f"\n### Edital {eid} — {card.get('title', '')}"]
            if card.get("objective"):
                parts.append(f"Objetivo: {card['objective']}")
            if card.get("deadline"):
                parts.append(f"Prazo: {card['deadline']}")
            if card.get("mechanism"):
                parts.append(f"Mecanismo: {card['mechanism']}")
            if card.get("eligible_entities"):
                parts.append(
                    f"Elegíveis: {', '.join(card['eligible_entities'])}"
                )
            vr = card.get("value_range") or {}
            if vr.get("min_brl") or vr.get("max_brl"):
                parts.append(
                    f"Valor: R${vr.get('min_brl', '?')} – R${vr.get('max_brl', '?')}"
                )
            tr = card.get("trl_range") or {}
            if tr.get("min") is not None or tr.get("max") is not None:
                parts.append(f"TRL: {tr.get('min', '?')}–{tr.get('max', '?')}")
            if card.get("counterpart_required") is not None:
                parts.append(
                    f"Contrapartida: {'sim' if card['counterpart_required'] else 'não'}"
                )
            for req in (card.get("key_requirements") or [])[:5]:
                parts.append(f"• {req}")
            for fact in (card.get("key_facts") or [])[:4]:
                parts.append(f"– {fact}")
            blocks.append("\n".join(parts))
        if not blocks:
            return ""
        return "\nDETALHES DOS EDITAIS EM FOCO:" + "\n".join(blocks) + "\n"

    def explore(
        self,
        message: str,
        history: list[dict] | None = None,
        edital_ids: list[str] | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
        agent_enabled: bool = False,
    ) -> str:
        """Dispatcher do chat stateless sobre o catálogo."""
        if agent_enabled:
            return self._explore_agent(
                message, history, edital_ids, node_id, node_type,
            )
        return self._explore_legacy(
            message, history, edital_ids, node_id, node_type,
        )

    def _build_legacy_messages(
        self,
        message: str,
        history: list[dict] | None,
        edital_ids: list[str] | None,
        node_id: str | None,
        node_type: str | None,
        *,
        system: str = EXPLORE_SYSTEM_PROMPT,
    ) -> list[dict]:
        """Monta as mensagens do explore legacy (catálogo + detalhes + histórico)."""
        self._ensure_client()
        index_str = self._get_index_for_prompt()

        scope_ids: list[str] | None = None
        if node_id and node_type:
            primary = (edital_ids[0] if edital_ids and node_type == "edital" else None)
            scope_ids = self._graph_service.resolve_scope(
                edital_id=primary, node_id=node_id, node_type=node_type,
            )

        focus_ids = self._resolve_focus_ids(message, scope_ids or edital_ids)
        details_block = self._build_edital_details(focus_ids)

        messages: list[dict] = [{"role": "system", "content": system}]
        for turn in (history or [])[-8:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({
            "role": "user",
            "content": EXPLORE_USER_PROMPT.format(
                index_json=index_str,
                details_block=details_block,
                message=message,
            ),
        })
        return messages

    def _explore_legacy(
        self,
        message: str,
        history: list[dict] | None,
        edital_ids: list[str] | None,
        node_id: str | None,
        node_type: str | None,
    ) -> str:
        """Pipeline original (pre-Sprint 3): catálogo inteiro injetado no prompt
        + 1 LLM call. Mantido durante o rollout do agente."""
        messages = self._build_legacy_messages(
            message, history, edital_ids, node_id, node_type,
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.3,
                max_tokens=1200,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Erro LLM no explore: %s", e)
            return "Desculpe, não consegui processar agora. Tente novamente em instantes."

    def explore_turn(
        self,
        message: str,
        history: list[dict] | None = None,
        edital_ids: list[str] | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
    ) -> tuple[str, dict]:
        """Explore legacy + extração de `profile_updates` na MESMA chamada LLM."""
        system = EXPLORE_SYSTEM_PROMPT + EXPLORE_PROFILE_EXTRACTION_INSTRUCTION
        messages = self._build_legacy_messages(
            message, history, edital_ids, node_id, node_type, system=system,
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.3,
                max_tokens=1400,
                response_format={"type": "json_object"},
            )
            raw = (response.choices[0].message.content or "").strip()
            data = json.loads(raw)
            answer = (data.get("answer") or "").strip()
            if not answer:
                raise ValueError("explore_turn: JSON sem 'answer' útil")
            updates = data.get("profile_updates")
            return answer, updates if isinstance(updates, dict) else {}
        except Exception as e:
            logger.warning(
                "explore_turn: extração unificada falhou (%s) — fallback texto puro", e,
            )
            return (
                self._explore_legacy(message, history, edital_ids, node_id, node_type),
                {},
            )

    def _explore_tools(self) -> list:
        """Tools do agente de explore: leitura cross-dim + planejamento, e
        opcionalmente deep_research (subagente web)."""
        from core.llm.agent_tools import build_explore_tools
        from core.llm.agent_tools.planning_tools import PlanState, build_planning_tools

        tools = build_explore_tools(self, self._graph_service) + build_planning_tools(PlanState())
        if os.getenv("EXPLORE_DEEP_RESEARCH_ENABLED", "false").lower() == "true":
            from core.llm.agent_tools.research_tools import build_research_tools
            tools = tools + build_research_tools()
        return tools

    def _explore_agent(
        self,
        message: str,
        history: list[dict] | None,
        edital_ids: list[str] | None,
        node_id: str | None,
        node_type: str | None,
    ) -> str:
        """Pipeline agente (Sprint 3 do Cenário B): run_agent + tools cross-dim,
        planejamento e (gated) deep_research — montadas em `_explore_tools`."""
        from core.llm.agent_runtime import resolve_agent_provider, run_agent

        self._load_index()

        messages: list[dict] = []
        for turn in (history or [])[-8:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        hint = self._build_explore_hint(edital_ids, node_id, node_type)
        if hint:
            messages.append({"role": "user", "content": hint})

        messages.append({"role": "user", "content": message})

        tools = self._explore_tools()
        provider, model = resolve_agent_provider(
            "anthropic", ANTHROPIC_MODEL_AGENT_EXPLORE,
        )
        result = run_agent(
            system=EXPLORE_AGENT_SYSTEM,
            initial_messages=messages,
            tools=tools,
            model=model,
            provider=provider,
            max_steps=EXPLORE_AGENT_MAX_STEPS,
        )

        if result.stop_reason == "error":
            logger.error("explore agent: stop_reason=error após %d steps", len(result.steps))
            return "Desculpe, não consegui processar agora. Tente novamente em instantes."

        return result.final_text or "Não consegui formular uma resposta agora."

    @staticmethod
    def _build_explore_hint(
        edital_ids: list[str] | None,
        node_id: str | None,
        node_type: str | None,
    ) -> str:
        """Constrói hint textual do clique no grafo para o agente."""
        parts: list[str] = []
        if node_id and node_type:
            parts.append(
                f"[Contexto: o visitante está focado no nó '{node_id}' "
                f"(tipo={node_type}). Considere usar get_graph_neighbors ou "
                f"get_edital com isso, conforme a pergunta.]"
            )
        if edital_ids:
            ids_str = ", ".join(str(i) for i in edital_ids[:3])
            parts.append(
                f"[Contexto: visitante mencionou ou clicou nos editais: {ids_str}. "
                f"Considere usar get_edital ou find_analogues nesses IDs.]"
            )
        return "\n".join(parts)

    def _parse_matches(self, raw: str) -> list[dict]:
        """Extrai lista de matches do JSON retornado pela LLM."""
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    raw = part
                    break

        try:
            data = json.loads(raw)
            matches = data.get("matches", [])
            if isinstance(matches, list):
                return matches
        except json.JSONDecodeError:
            m = re.search(r'"matches"\s*:\s*(\[.*?\])', raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass

        logger.warning("Não foi possível parsear resposta do matching: %s", raw[:300])
        return []
