"""
ExploreAgent — Chat exploratório sobre o catálogo + matching Karpathy-style.

3 rotas internas (classificador determinístico):
  - factual: GraphService + readers, zero LLM (perguntas de catálogo)
  - reasoning: 1 LLM call (conceitual)
  - agent: multi-step com tools (precisa de dados + raciocínio)

Extraído de KGMatchService (Fase 1 da spec match-evolution.md).
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

EXPLORE_LOG_INSTRUCTION = """

MEMÓRIA ENTRE SESSÕES (log_exploration_decision)
- Quando você concluir que um edital é uma boa oportunidade para este usuário,
  registre com log_exploration_decision(edital_id, "recommended", reason). Quando
  concluir que não serve, registre com decision="discarded" e uma razão curta.
- Registre só decisões com base — não logue cada edital citado de passagem.
- Revisitar o mesmo edital atualiza a decisão (a última prevalece); pode rechamar."""


EXPLORE_MATCH_INSTRUCTION = """

MATCH COM O PERFIL (find_matching_editais)
- Este usuário TEM perfil preenchido. Quando ele pedir oportunidades para a
  empresa ("quais editais servem para mim?", "o que tem para a gente?"), ou logo
  ao abrir uma conversa com perfil, chame find_matching_editais e apresente os
  editais com a justificativa de cada match (o que da empresa casou com o quê).
- É afinidade temática (conteúdo), NÃO elegibilidade dura: apresente como ponto de
  partida e deixe a decisão com o usuário. Use get_edital para aprofundar um match."""

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
        has_profile: bool = False,
        profile_text: str | None = None,
        workspace_id: str | None = None,
        db=None,
    ) -> str:
        """Dispatcher: classifica intenção e roteia para a rota adequada.

        - factual: GraphService + readers, zero LLM
        - reasoning: 1 LLM call (_explore_legacy)
        - agent: multi-step com tools (_explore_agent)
        """
        intent = self._classify_intent(
            message, has_profile=has_profile, has_edital_ids=bool(edital_ids),
        )
        # Factual: tenta sem LLM, fallback → reasoning
        if intent == "factual":
            answer = self._factual_route(message, history, edital_ids, node_id, node_type)
            if answer is not None:
                return answer
            intent = "reasoning"

        if intent == "reasoning":
            return self._explore_legacy(message, history, edital_ids, node_id, node_type)

        return self._explore_agent(
            message, history, edital_ids, node_id, node_type,
            profile_text=profile_text, workspace_id=workspace_id, db=db,
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
        profile_text: str | None = None,
        workspace_id: str | None = None,
        db=None,
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
        system = EXPLORE_AGENT_SYSTEM

        # Match cross-domínio (hipergrado): só quando há PERFIL. A tool extrai os
        # nós da empresa do perfil (cacheado por hash) e rankeia editais por
        # afinidade de conteúdo. Independe de workspace/db (perfil vem do request).
        if profile_text:
            from core.llm.agent_tools.match_tools import build_match_tools
            tools = tools + build_match_tools(profile_text)
            system = system + EXPLORE_MATCH_INSTRUCTION

        # Memória do ExploreAgent (Fase 3A): só com workspace autenticado + db.
        # O bloco de decisões vai no SYSTEM (prefixo estável, antes do histórico
        # da conversa — D6); a tool de escrita é registrada junto.
        if db is not None and workspace_id:
            from core.llm.agent_tools.explore_tools import (
                build_exploration_log_tools,
                load_recent_exploration_decisions,
            )
            tools = tools + build_exploration_log_tools(db, workspace_id)
            system = system + EXPLORE_LOG_INSTRUCTION
            prior = load_recent_exploration_decisions(db, workspace_id)
            if prior:
                system = f"{system}\n\n{prior}"

        provider, model = resolve_agent_provider(
            "anthropic", ANTHROPIC_MODEL_AGENT_EXPLORE,
        )
        result = run_agent(
            system=system,
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

    # ------------------------------------------------------------------
    # Classificador de intenção
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_intent(
        message: str,
        *,
        has_profile: bool = False,
        has_edital_ids: bool = False,
    ) -> str:
        """Classifica a mensagem em 'factual', 'reasoning' ou 'agent'.

        Conservador: só rotorna 'factual' quando é inequívoco que a
        pergunta pode ser respondida sem LLM. Todo o resto vai para
        LLM (reasoning = 1 call, agent = multi-step).
        """
        msg = message.strip().lower()

        # Perfil presente → precisa cruzar perfil com catálogo → agente
        if has_profile:
            return "agent"

        # Tem IDs de edital (clique no grafo) + pergunta simples → factual
        if has_edital_ids:
            if re.match(
                r"^(mostra|exibe|abre|detalhes?\s+(do|sobre)|volta|abrir)\b",
                msg,
            ):
                return "factual"
            return "agent"

        # Pergunta sobre entidade do grafo sem ser edital (ICT, investidor)
        if re.search(r"\b(icts?|institui[çc]ão|fundo|investidor|embrapii|sebrae)\b", msg):
            return "agent"

        # Precisa de dados + raciocínio → agente multi-step
        if re.search(
            r"\b(compare|melhor|recomende|qual combina|pra mim|"
            r"minha empresa|sugira|ajuda|recomendação|"
            r"oportunidade|indica|viável|vale a pena)\b",
            msg,
        ):
            return "agent"

        # Pergunta factual pura
        if re.match(
            r"^(quais?|quantos?|lista|mostra|exibe|tem|existe|"
            r"abertos?|aberta|filtra|busca)\b",
            msg,
        ):
            return "factual"

        # Pergunta conceitual → 1 LLM call basta
        if re.match(
            r"^(o que é|como funciona|explique|qual a diferença|"
            r"defina|o que significa|qual o conceito)",
            msg,
        ):
            return "reasoning"

        # Fallback conservador: reasoning (1 call, barato)
        return "reasoning"

    # ------------------------------------------------------------------
    # Factual route
    # ------------------------------------------------------------------

    def _factual_route(
        self,
        message: str,
        history: list[dict] | None = None,
        edital_ids: list[str] | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
    ) -> str | None:
        """Tenta responder sem LLM. Retorna markdown ou None (fallback)."""
        msg = message.strip().lower()
        self._load_index()

        # --- (1) "mostra edital 589" / "detalhes do finep:589" -----------
        for eid in re.findall(
            r"(?:edital\s+)?([a-z]+:\d[\w-]+|\b\d{3,5}\b)", msg,
        ):
            known = {str(e["id"]) for e in self._index.get("editais", [])}
            # Tenta match exato; se falhar, busca por sufixo numérico
            candidates = [eid] if eid in known else [
                k for k in known if k.split(":")[-1] == eid
            ]
            for cid in candidates:
                card = self.get_edital_by_id(cid)
                if card:
                    return self._format_edital_card(card)

        # --- (2) "quantos editais?" ------------------------------------
        if re.match(r"^quantos?\b", msg):
            stats = self.get_stats()
            return (
                f"**{stats['total_editais']} editais** no catálogo · "
                f"{stats['by_status'].get('ABERTA', 0)} abertos · "
                f"{stats['n_themes']} temas · {stats['n_fontes']} fontes"
            )

        # --- (3) "editais de saúde" / "filtra tema X" ------------------
        tema = None
        tm = re.search(r"(?:de|sobre|tema|área|em)\s+([a-zà-ú\s]+?)(?:\s*$|\.)", msg)
        if tm:
            candidate = tm.group(1).strip()
            if len(candidate) >= 3:
                tema = candidate

        status_filter = None
        if re.search(r"\babertos?\b", msg):
            status_filter = "ABERTA"
        if re.search(r"\b(encerrados?|fechados?)\b", msg):
            status_filter = "ENCERRADA"

        results = self.list_editais(status=status_filter, tema=tema, limit=20)

        if results:
            label_parts = []
            if status_filter:
                label_parts.append(status_filter.capitalize())
            if tema:
                label_parts.append(tema.capitalize())
            label = " · ".join(label_parts) if label_parts else "Editais"
            return self._format_edital_table(results, label)
        elif tema or status_filter:
            # Tema específico sem resultado: fallback para LLM
            return None

        # --- (4) fallback: não entendeu o padrão -----------------------
        return None

    # ------------------------------------------------------------------
    # Formatadores (factual route)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_edital_card(card: dict) -> str:
        title = card.get("title", card.get("id", ""))
        lines = [f"### {title}"]
        lines.append(f"**ID:** {card.get('id', '?')}")
        lines.append(f"**Status:** {card.get('status', '?')}")
        if card.get("deadline"):
            lines.append(f"**Prazo:** {card['deadline']}")
        if card.get("mechanism"):
            lines.append(f"**Mecanismo:** {card['mechanism']}")
        if card.get("eligible_entities"):
            lines.append(f"**Elegíveis:** {', '.join(card['eligible_entities'])}")
        vr = card.get("value_range") or {}
        if vr.get("min_brl") or vr.get("max_brl"):
            lines.append(f"**Valor:** R${vr.get('min_brl', '?')} – R${vr.get('max_brl', '?')}")
        if card.get("objective"):
            lines.append("")
            lines.append(card["objective"])
        for i, req in enumerate((card.get("key_requirements") or [])[:5], 1):
            lines.append(f"{i}. {req}")
        return "\n".join(lines)

    @staticmethod
    def _format_edital_table(editais: list[dict], label: str) -> str:
        if not editais:
            return ""
        lines = [f"**{label}** ({len(editais)})", ""]
        lines.append("| ID | Título | Status | Prazo |")
        lines.append("|---|---|---|---|")
        for e in editais[:20]:
            eid = e.get("id", "?")
            title = (e.get("title") or "?")[:50]
            status = e.get("status", "?")
            deadline = e.get("deadline", "?")
            lines.append(f"| {eid} | {title} | {status} | {deadline} |")
        if len(editais) > 20:
            lines.append(f"\n*Mostrando 20 de {len(editais)} resultados*")
        return "\n".join(lines)

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
