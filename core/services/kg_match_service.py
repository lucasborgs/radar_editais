"""
KGMatchService — Matching Karpathy-style.

A LLM lê o índice completo de editais FINEP (data/knowledge_graph/index.json)
junto com o perfil da empresa e retorna os editais mais relevantes com
justificativa por dimensão. Para o top-3, enriquece com dados do card
(data/knowledge_graph/cards/{id}.json) quando disponível.

Sem embeddings, sem ChromaDB — apenas raciocínio LLM sobre índice estruturado.
"""
from __future__ import annotations

import json
import logging
import os
import re

from config import OBSIDIAN_VAULT_DIR
from core.kg import kg_store
from core.kg.edital_id import id_to_slug, slug_to_id
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

# =============================================================================
# PROMPT DE MATCHING
# =============================================================================

MATCH_SYSTEM_PROMPT = """Você é um especialista em fomento à inovação no Brasil com profundo
conhecimento das chamadas públicas FINEP, FNDCT e programas de CT&I.

Sua tarefa é analisar o perfil de uma empresa e identificar os editais FINEP mais relevantes
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
empresas a oportunidades de fomento público no Brasil (FINEP, FNDCT, CT&I).

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

EXPLORE_USER_PROMPT = """CATÁLOGO DE EDITAIS FINEP:
{index_json}
{details_block}
PERGUNTA DO VISITANTE:
{message}"""


# Extração de perfil ACOPLADA ao turno do front-door (decisão 2026-06-21): em vez
# de uma 2ª chamada LLM (extract_diff_from_message) sobre a mesma mensagem, o MESMO
# turno do explore devolve resposta + profile_updates num único JSON. Anexado ao
# EXPLORE_SYSTEM_PROMPT só no caminho `explore_turn`. As chaves espelham
# _DIFF_FIELD_LABELS de core.profile_extractor (o _build_diff filtra desconhecidos).
# NB: esta string nunca passa por .format() — chaves literais com 1 par de {}.
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


# Sistema prompt do modo agente (Sprint 3 do Cenário B). Substitui o
# EXPLORE_SYSTEM_PROMPT quando agent_enabled=True. As ferramentas de leitura são
# registradas via core.llm.agent_tools.build_explore_tools (8 tools cross-dim) e
# a de planejamento via build_planning_tools (write_todos) — ver _explore_agent.
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


# Sufixo de memória (Fase 3A): só anexado ao system quando há workspace
# autenticado (a tool log_exploration_decision está registrada). Sem workspace
# (/kg-explore público) este bloco não aparece e a tool não existe.
EXPLORE_LOG_INSTRUCTION = """

MEMÓRIA ENTRE SESSÕES (log_exploration_decision)
- Quando você concluir que um edital é uma boa oportunidade para este usuário,
  registre com log_exploration_decision(edital_id, "recommended", reason). Quando
  concluir que não serve, registre com decision="discarded" e uma razão curta.
- Registre só decisões com base — não logue cada edital citado de passagem.
- Revisitar o mesmo edital atualiza a decisão (a última prevalece); pode rechamar."""


# Anthropic Sonnet 4.6 (D1 híbrido). Configurável via env. Sprint 3 herda o
# default ANTHROPIC_MODEL_AGENT da WritingSession, mas exposto separado para
# permitir testar modelos diferentes nos 2 agentes.
ANTHROPIC_MODEL_AGENT_EXPLORE = os.getenv(
    "ANTHROPIC_MODEL_AGENT_EXPLORE",
    os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6"),
)
# 10 (era 6): planejamento (write_todos) + perguntas multi-parte cross-dim
# consomem mais passos — write_todos em si gasta um, e cada dimensão pode pedir
# uma tool. Configurável via env.
EXPLORE_AGENT_MAX_STEPS = int(os.getenv("EXPLORE_AGENT_MAX_STEPS", "10"))

MATCH_USER_PROMPT = """PERFIL DA EMPRESA:
{profile_context}

CATÁLOGO DE EDITAIS FINEP:
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

class KGMatchService:
    """Matching de empresa↔editais via LLM sobre o índice FINEP."""

    def __init__(self):
        self._index: dict = {}
        self._client = None
        self._model = ""
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
        """Retorna top_k editais mais relevantes para o perfil da empresa.

        Fluxo:
        1. LLM lê índice completo + perfil → lista rankeada
        2. Para o top-3 com card disponível → enriquece com key_requirements
        """
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

        # Enriquece top-3 com dados do card
        for match in matches[:3]:
            card = self.get_edital_by_id(match["id"])
            if card and card.get("key_requirements"):
                match["key_requirements"] = card["key_requirements"]
            if card and card.get("objective"):
                match["objective"] = card["objective"]

        return matches

    # ------------------------------------------------------------------
    # Grafo (Dashboard — Obsidian-style)
    # ------------------------------------------------------------------

    _WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
    _FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

    def _parse_frontmatter(self, text: str) -> dict:
        """Parse mínimo do YAML frontmatter — só os escalares que usamos."""
        m = self._FRONTMATTER_RE.match(text)
        if not m:
            return {}
        fm: dict = {}
        for line in m.group(1).splitlines():
            if ":" not in line or line.lstrip().startswith("-"):
                continue
            key, _, val = line.partition(":")
            val = val.strip().strip('"').strip("'")
            if val:
                fm[key.strip()] = val
        return fm

    def _folder_type_map(self) -> dict[str, str]:
        """folder (plural, no vault) → tipo de nó (chave do schema §6.1).

        Derivado de wiki_schema.node_types() — schema autoritativo, sem
        duplicação. `home` (folder="") é tratado à parte.
        """
        from core.kg import wiki_schema
        return {
            v["folder"]: k
            for k, v in wiki_schema.node_types().items()
            if v.get("folder")
        }

    def _node_type_for_parts(self, parts: tuple[str, ...]) -> str | None:
        """parts = node_id.split('/'). Retorna tipo ou None (fora do schema)."""
        if len(parts) < 3:
            return "home" if parts[-1] == "HOME" else None
        return self._folder_type_map().get(parts[1])

    def get_graph(self) -> dict:
        """Constrói o grafo a partir do vault Obsidian unificado no projeto.

        Faz parse das notas .md: frontmatter (título/status) + wikilinks
        `[[target|alias]]` viram arestas. Os tipos de nó vêm de
        wiki_schema.node_types() (WIKI.md §6.1) — notas/links de pastas que
        não são tipo de nó (ex.: dimensões rebaixadas a tag, §6.1.1) são
        ignorados, mesmo que existam no vault como artefato stale. Arestas são
        não-direcionadas e dedupadas (edital↔tema aparece nos dois sentidos).
        """
        vault = OBSIDIAN_VAULT_DIR
        if not vault.exists():
            logger.warning("Vault Obsidian não encontrado: %s", vault)
            return {"nodes": [], "links": []}

        nodes: dict[str, dict] = {}
        edges: set[tuple[str, str]] = set()

        for path in sorted(vault.rglob("*.md")):
            rel = path.relative_to(vault).with_suffix("")
            node_id = "/".join(rel.parts)
            ntype = self._node_type_for_parts(rel.parts)
            if ntype is None:  # pasta fora do schema (tag rebaixada / stray)
                continue

            text = path.read_text(encoding="utf-8")
            fm = self._parse_frontmatter(text)
            label = "FINEP" if ntype == "home" else (fm.get("title") or path.stem)
            node: dict = {
                "id": node_id,
                "type": ntype,
                "label": label,
            }
            if ntype == "edital":
                # edital_id é o id real prefixado (`finep:589`), lido do
                # frontmatter — o nome do arquivo é colon-free (`finep-589`)
                # porque o Obsidian proíbe `:`. O frontend usa este edital_id
                # para chamar o explore/get_edital, que espera o id prefixado.
                node["edital_id"] = fm.get("chamada_id") or path.stem
                node["status"] = fm.get("status", "Desconhecido")
            nodes[node_id] = node

            for target, _alias in self._WIKILINK_RE.findall(text):
                target = target.strip()
                if target.endswith("/"):  # link de pasta (nav HOME) — sem aresta
                    continue
                if self._node_type_for_parts(tuple(target.split("/"))) is None:
                    continue  # alvo fora do schema (ex.: nó rebaixado a tag)
                a, b = sorted((node_id, target))
                edges.add((a, b))

        # Garante nó pra qualquer alvo de wikilink sem arquivo próprio.
        for a, b in edges:
            for nid in (a, b):
                if nid not in nodes:
                    seg = tuple(nid.split("/"))
                    nodes[nid] = {
                        "id": nid,
                        "type": self._node_type_for_parts(seg) or "outro",
                        "label": seg[-1],
                    }

        links = [{"source": a, "target": b} for a, b in sorted(edges)]
        return {"nodes": list(nodes.values()), "links": links}

    # ------------------------------------------------------------------
    # Explore (Dashboard — chat sem perfil)
    # ------------------------------------------------------------------

    def _resolve_focus_ids(
        self, message: str, explicit_ids: list[str] | None
    ) -> list[str]:
        """IDs de editais em foco: os explícitos (clique) + os citados no texto.

        Só conta números que correspondem a IDs reais do índice — evita falsos
        positivos com anos (2024) ou quantidades. Cap em 3 pra controlar prompt.
        """
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

    def _edital_ids_for_node(self, node_id: str) -> list[str]:
        """Extrai IDs de editais ligados ao nó via wikilinks no MD do vault."""
        path = OBSIDIAN_VAULT_DIR / f"{node_id}.md"
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        ids = []
        for target, _ in self._WIKILINK_RE.findall(text):
            parts = target.strip().split("/")
            if len(parts) >= 2 and parts[-2] == "editais":
                # O wikilink aponta para o slug colon-free (`finep-589`);
                # devolvemos o id real prefixado (`finep:589`) que o resto do
                # sistema (retrieve_chunks, get_edital_by_id) espera.
                ids.append(slug_to_id(parts[-1]))
        return ids

    def _find_analogue_ids(self, edital_id: str) -> list[str]:
        """Traversal reverso: edital → temas/publicos/subprogramas → editais análogos.

        Lê a wiki page do edital no vault, segue wikilinks para nós não-edital, e
        coleta os editais ligados a cada um. Retorna os IDs análogos (sem o
        próprio edital_id), preservando ordem de descoberta.
        """
        edital_path = OBSIDIAN_VAULT_DIR / f"radar-editais/editais/{id_to_slug(edital_id)}.md"
        if not edital_path.exists():
            return []

        text = edital_path.read_text(encoding="utf-8")
        folder_type = self._folder_type_map()

        neighbour_nodes: list[str] = []
        for target, _ in self._WIKILINK_RE.findall(text):
            target = target.strip()
            if target.endswith("/"):
                continue
            parts = target.split("/")
            if len(parts) < 3:
                continue
            folder = parts[1]
            if folder == "editais" or folder not in folder_type:
                continue
            neighbour_nodes.append(target)

        seen: set[str] = {str(edital_id)}
        analogues: list[str] = []
        for node_id in neighbour_nodes:
            for eid in self._edital_ids_for_node(node_id):
                if eid not in seen:
                    seen.add(eid)
                    analogues.append(eid)
        return analogues

    def resolve_scope(
        self,
        edital_id: str | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
        max_analogues: int = 3,
    ) -> list[str]:
        """Resolve trigger → list[edital_ids], com o ID primário primeiro.

        Regras:
          - node_type ∈ {tema, publico, subprograma, fonte, ...}: retorna os IDs
            que o nó liga via wikilinks (`_edital_ids_for_node`)
          - node_type == "edital" ou edital_id (sessão): retorna [primary] +
            análogos (até max_analogues) via traversal reverso
          - Sem trigger algum: retorna todos os edital_ids do índice
        """
        if node_id and node_type and node_type not in ("edital", "home", None):
            return self._edital_ids_for_node(node_id)

        primary = edital_id
        if node_id and node_type == "edital":
            # node_id termina no slug colon-free (`.../editais/finep-589`);
            # converte de volta ao id real prefixado para casar com chunks/cards.
            primary = slug_to_id(node_id.split("/")[-1])

        if primary:
            analogues = self._find_analogue_ids(primary)[:max_analogues]
            return [primary] + analogues

        self._load_index()
        return [str(e["id"]) for e in self._index.get("editais", [])]

    def explore(
        self,
        message: str,
        history: list[dict] | None = None,
        edital_ids: list[str] | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
        agent_enabled: bool = False,
        workspace_id: str | None = None,
        db=None,
    ) -> str:
        """Dispatcher do chat stateless sobre o catálogo.

        Quando `agent_enabled=True` (rollout do Sprint 3 do Cenário B), roda
        o agente Anthropic com 4 tools. Caso contrário, mantém o pipeline
        original (catálogo inteiro no prompt + 1 LLM call). O caller decide
        o flag — endpoint /explore lê de env / workspace conforme contexto.

        `workspace_id`/`db` (Fase 3A): memória do ExploreAgent entre sessões.
        Quando AMBOS estão presentes (só no caminho AUTENTICADO do front-door),
        o agente carrega as decisões anteriores no prefixo do prompt e ganha a
        tool log_exploration_decision. Ausentes (/kg-explore público, anônimo) →
        sem memória, comportamento de hoje. Ignorados no caminho legacy.
        """
        if agent_enabled:
            return self._explore_agent(
                message, history, edital_ids, node_id, node_type,
                workspace_id=workspace_id, db=db,
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
        """Monta as mensagens do explore legacy (catálogo + detalhes + histórico).

        Extraído de `_explore_legacy` para ser reaproveitado por `explore_turn`
        (front-door), que injeta um `system` aumentado com extração de perfil.
        """
        self._ensure_client()
        index_str = self._get_index_for_prompt()

        # Resolve escopo via resolve_scope: clique em nó-edital agora também
        # traz análogos (mesmo tema/publico) — antes o clique em edital trazia
        # só ele próprio. Para nós tema/publico, mantém comportamento (wikilinks
        # diretos). Sem clique, scope_ids fica None e caímos no comportamento
        # antigo (focus vem de `edital_ids` passado + detecção no texto).
        scope_ids: list[str] | None = None
        if node_id and node_type:
            primary = (edital_ids[0] if edital_ids and node_type == "edital" else None)
            scope_ids = self.resolve_scope(
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
        """Explore legacy + extração de `profile_updates` na MESMA chamada LLM.

        Front-door (decisão 2026-06-21): elimina a 2ª passada de LLM que o
        `extract_diff_from_message` fazia sobre a mesma mensagem. Uma única
        completion devolve, em JSON, a resposta ao visitante E os campos do
        CompanyProfile que a última mensagem preenche/altera. Retorna
        `(answer, profile_updates)`; `profile_updates` é {} quando não há fato
        novo — o modelo decide ("gate" emergente, sem regex nem 2ª chamada).

        Degrada com segurança: qualquer falha de JSON/LLM cai no `_explore_legacy`
        de texto puro (resposta preservada) com updates vazios — o diff daquele
        turno se perde, mas é recuperável no próximo (o perfil persiste).
        """
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
        opcionalmente deep_research (subagente web).

        PlanState próprio por turno (stateless entre chamadas), igual ao writing.
        `deep_research` fica atrás de EXPLORE_DEEP_RESEARCH_ENABLED (default off):
        o explore é endpoint PÚBLICO/não-auth e o crawl multi-step é vetor de
        custo — liga-se conscientemente.
        """
        from core.llm.agent_tools import build_explore_tools
        from core.llm.agent_tools.planning_tools import PlanState, build_planning_tools

        tools = build_explore_tools(self) + build_planning_tools(PlanState())
        if os.getenv("EXPLORE_DEEP_RESEARCH_ENABLED", "false").lower() == "true":
            from core.llm.agent_tools.research_tools import build_research_tools
            # Stateless de propósito: o explore é endpoint PÚBLICO/não-auth — não há
            # workspace_id nem db autenticado neste escopo. Sem onde (e sem permissão
            # RLS para) persistir findings, então mantém a Fase A aqui.
            tools = tools + build_research_tools()
        return tools

    def _explore_agent(
        self,
        message: str,
        history: list[dict] | None,
        edital_ids: list[str] | None,
        node_id: str | None,
        node_type: str | None,
        workspace_id: str | None = None,
        db=None,
    ) -> str:
        """Pipeline agente (Sprint 3 do Cenário B): run_agent + tools cross-dim,
        planejamento e (gated) deep_research — montadas em `_explore_tools`.

        Diferenças vs legacy:
          • Sem catálogo inteiro no prompt — agente busca via list_editais
          • Sem pré-resolução de focus_ids — agente decide via tools
          • Dica de clique no grafo vira message extra (não substitui análise)

        Memória entre sessões (Fase 3A): com `workspace_id`+`db` (caminho
        autenticado), o agente recebe as últimas decisões do workspace no prefixo
        ESTÁVEL do prompt (no system, antes do histórico — D6) e a tool
        log_exploration_decision. Sem eles, opera stateless como antes.
        """
        from core.llm.agent_runtime import resolve_agent_provider, run_agent

        self._load_index()  # garante índice carregado (não usa self._client)

        messages: list[dict] = []
        for turn in (history or [])[-8:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Dica de contexto: clique no grafo vira hint pro agente decidir o que
        # consultar. Sem clique, esta linha não é adicionada.
        hint = self._build_explore_hint(edital_ids, node_id, node_type)
        if hint:
            messages.append({"role": "user", "content": hint})

        messages.append({"role": "user", "content": message})

        tools = self._explore_tools()

        # Memória do ExploreAgent (Fase 3A): só com workspace autenticado + db.
        # O bloco de decisões vai no SYSTEM (prefixo estável, antes do histórico
        # da conversa — D6); a tool de escrita é registrada junto.
        system = EXPLORE_AGENT_SYSTEM
        if db is not None and workspace_id:
            from core.llm.agent_tools.explore_tools import (
                build_exploration_log_tools,
                load_recent_exploration_decisions,
            )
            tools = tools + build_exploration_log_tools(db, workspace_id)
            system = EXPLORE_AGENT_SYSTEM + EXPLORE_LOG_INSTRUCTION
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
        """Constrói hint textual do clique no grafo para o agente.

        O agente decide se vai usar (chamar get_edital, get_graph_neighbors,
        etc.) ou ignorar caso a pergunta seja sobre outro tópico.
        """
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
        # Remove possível markdown
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
            # Tenta extrair JSON com regex
            m = re.search(r'"matches"\s*:\s*(\[.*?\])', raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass

        logger.warning("Não foi possível parsear resposta do matching: %s", raw[:300])
        return []
