"""Hyper-Extract — extração de hipergrado N-ário por edital (arquitetura hipergrado).

Wrapper de produção sobre a lib `hyperextract` (`AutoHypergraph`). Lê o silver
`*.jsonl` de UM edital, extrai um hipergrafo com o schema de produção (12 tipos
de nó, 10 tipos de aresta, todos `Literal`) e grava
`data/knowledge_graph/hypergraphs/{edital_id}.json` no formato `{nodes, edges}`.

Substitui (em paralelo, sem remover nada ainda) a síntese LLM de wiki_pages.
Ver `docs/specs/hypergraph-architecture.md` — Sprint 0.

Decisões de integração (vs. o experimento `scripts/hyper_extract_test.py`):

* **Embedder canônico, não `langchain_openai`.** `_CanonicalEmbeddings` delega a
  `core.retrieval.embedder.embed_texts/embed_query`, então o hipergrado embeda no
  MESMO modelo/dimensão (text-embedding-3-small, 1536d) que o resto do sistema —
  invariante de coluna única do projeto. Nunca instanciar `OpenAIEmbeddings`.

* **LLM via config canônica.** `AutoHypergraph` exige um `BaseChatModel` LangChain
  (chama `.with_structured_output()`), então não dá para passar o `OpenAI` cru de
  `make_client()`. `_build_llm()` constrói um `ChatOpenAI` espelhando os knobs de
  resiliência de `core.llm.llm_client` (timeout/retries/base_url) + os envs do
  projeto (`OPENAI_MODEL`/`OPENAI_API_KEY`) — mesmo padrão de
  `core/llm/agent_graph.py::_build_chat_model`.

* **Modo two_stage ignora `prompt=`.** Em two_stage a lib usa `node_prompt`/
  `edge_prompt` (templates com `{source_text}`/`{known_nodes}`), NÃO o `prompt`.
  Por isso o domínio vai em `prompt_for_node_extraction`/`prompt_for_edge_extraction`
  — caso contrário a lib cairia nos defaults genéricos em inglês.

* **Dedup case-insensitive consistente.** `_prune_dangling_edges` compara os
  membros da aresta (via `nodes_in_edge_extractor`) contra as chaves de nó (via
  `node_key_extractor`). Se só a chave de nó fosse minúscula, TODA aresta seria
  podada como dangling. Os três extractors normalizam juntos (`_norm`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from config import BRONZE_DIR, KNOWLEDGE_GRAPH_DIR, SILVER_DIR
from core.llm import llm_client as _llm_cfg
from core.retrieval.embedder import embed_query, embed_texts

logger = logging.getLogger(__name__)

STRUCTURED_DOCS_DIR = SILVER_DIR / "structured_docs"
HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"

# Espelha core.retrieval.chunker._DROP_KINDS: blocos que o structurer já marca
# como ruído (rodapé/assinatura/navegação de página web). O RAG os descarta;
# o Hyper-Extract também — senão o menu do site vira entidade (ex.: FAPESP web).
_DROP_KINDS = {"boilerplate", "signature"}

# Heading "parece título de chamada". Usado p/ escolher o nome canônico do Edital
# resistindo a navegação web ("Histórico") e a cláusulas mal-classificadas como
# heading ("8.1. Será destinado…"). _TITLE_START_RE (ancorado) é o sinal forte —
# títulos COMEÇAM com a palavra-chave; cláusulas começam com número de seção.
_TITLE_START_RE = re.compile(
    r"^\s*(edital|chamada|seleç|selec|programa|auxíli|auxili|fomento|proposta|concurso)",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(
    r"edital|chamada|auxíli|auxili|subvenç|subvenc|seleç|selec|programa|fomento|propostas",
    re.IGNORECASE,
)

# Modelo de extração: FIXO em gpt-4o-mini, desacoplado do OPENAI_MODEL global.
# Trocar o tier 3 (LLM_BACKEND/OPENAI_MODEL) NÃO deve mudar a extração de
# hipergrado: o cache por source_hash não invalida por modelo, então herdar
# OPENAI_MODEL serviria extração inconsistente em prod. Override consciente só via
# HYPER_EXTRACT_MODEL (ex.: apontar a um free-tier após re-extração deliberada).
HYPER_EXTRACT_MODEL = os.environ.get("HYPER_EXTRACT_MODEL", "gpt-4o-mini")
# Cap de chars por edital. Default 0 = SEM cap (Opção C): um cap fixo é cego à
# relevância — cortava 39% do texto, incluindo núcleo (Regulamento, detalhamento
# de temas, cauda do edital) quando a ordem dos docs não era núcleo-primeiro.
# Em vez disso dropamos docs-formulário (skip-list) e passamos o resto inteiro.
# Continua disponível como botão de custo p/ debug (env > 0).
HYPER_EXTRACT_MAX_CHARS = int(os.environ.get("HYPER_EXTRACT_MAX_CHARS", "0"))

_CHUNK_SIZE = 2048
_CHUNK_OVERLAP = 256
_MAX_WORKERS = 4


# ── schema de produção ────────────────────────────────────────────────────────

NodeType = Literal[
    "Fonte",       # FINEP, FAPESP, FAPESC — atribuição e agrupamento
    "Edital",      # a chamada pública (carrega título/prazo/status/valor/fonte)
    "Programa",    # PIPE, PAPPE, FNDCT — conexão transversal entre editais
    "ICT",         # institutos, universidades — parceria obrigatória/oportunidade
    "Investidor",  # FIPs, VCs — trilho investidor
    "Tema",        # domínio de aplicação (saúde, agronegócio, manufatura 4.0)
    "Tecnologia",  # capacidade técnica (IA, IoT, visão computacional)
    "Aplicação",   # caso de uso específico (triagem, classificação de grãos)
    "Mecanismo",   # subvenção, crédito, investimento
    "Requisito",   # constraint de elegibilidade (TRL, porte, idade, região, prazo)
    "Exclusão",    # exclusão explícita
    "Entidade",    # tipo de organização elegível (empresa, startup, ICT)
]

EdgeType = Literal[
    "financia",     # Fonte/Edital → Entidade
    "exige",        # Edital/Programa → Requisito
    "abrange_tema", # Edital/Programa → Tema/Tecnologia
    "aplica_em",    # Tecnologia → Aplicação
    "destina_a",    # Edital → Entidade
    "exclui",       # Edital → Exclusão
    "parceria_com", # Edital → ICT/Investidor
    "pertence_a",   # Edital → Programa → Fonte
    "viabiliza",    # ICT/Tecnologia → Aplicação/Tema
    "resolve",      # Tecnologia/Aplicação → Desafio/Tema
]


class HyperNode(BaseModel):
    """Nó do hipergrado. Para nós `Edital`, os campos de display
    (prazo/status/valor/fonte) carregam as propriedades antes na wiki_page."""

    name: str = Field(description="Nome canônico da entidade. Para Edital, é o título da chamada.")
    type: NodeType = Field(description="Categoria da entidade no ecossistema de fomento")
    description: str = Field(description="Descrição breve no contexto do edital")
    # Propriedades de display — preencher SOMENTE para nós do tipo Edital.
    prazo: str | None = Field(
        default=None,
        description="SÓ para Edital: prazo/data limite de submissão (cronograma). null nos demais tipos.",
    )
    status: str | None = Field(
        default=None,
        description="SÓ para Edital: situação (aberto, encerrado, previsto). null nos demais tipos.",
    )
    valor: str | None = Field(
        default=None,
        description="SÓ para Edital: valor/recurso disponível (teto por projeto ou total). null nos demais tipos.",
    )
    fonte: str | None = Field(
        default=None,
        description="SÓ para Edital: agência financiadora (FINEP, FAPESP, FAPESC). null nos demais tipos.",
    )


class HyperEdge(BaseModel):
    """Hiperaresta N-ária: conecta 2+ nós simultaneamente."""

    type: EdgeType = Field(description="Tipo da relação hipergráfica")
    members: list[str] = Field(
        description="Nomes de TODAS as entidades participantes (mínimo 2; priorize 3+)"
    )
    description: str = Field(description="Descrição da relação hipergráfica")


# ── prompts de domínio (two_stage: node + edge) ───────────────────────────────

_NODE_PROMPT = """\
Você está analisando UM documento do ecossistema brasileiro de fomento público à
pesquisa e inovação (um edital/chamada). Extraia TODAS as entidades distintas
MENCIONADAS NO TEXTO DE ORIGEM abaixo, cada uma com seu tipo.

REGRA CRÍTICA: extraia SOMENTE entidades que aparecem no TEXTO DE ORIGEM. As
definições de tipo abaixo são descrições — NUNCA extraia um nome citado nelas a
menos que ele também apareça no texto.

TIPOS DE ENTIDADE (cada entidade recebe exatamente um):
- Fonte: a agência/fundação financiadora citada no texto (quem banca a chamada)
- Edital: a chamada pública que ESTE documento descreve (título + número). Para o
  nó Edital preencha prazo (data limite de submissão), status (aberto/encerrado/
  previsto), valor (recurso disponível) e fonte (agência) quando o texto informar.
  Esses quatro campos ficam null em todos os outros tipos.
- Programa: o programa guarda-chuva ao qual a chamada pertence, se citado
- ICT: instituto, universidade ou centro de pesquisa parceiro citado
- Investidor: fundo de venture capital, CVC, FIP ou aceleradora citado
- Tema: domínio de aplicação amplo tratado pela chamada
- Tecnologia: capacidade técnica específica citada (uma ferramenta/método)
- Aplicação: CASO DE USO concreto onde uma tecnologia atua — é o tipo central
  para conectar domínios diferentes. Conceitualmente é uma OPERAÇÃO (triagem,
  classificação, diagnóstico, detecção, previsão, monitoramento, otimização)
  aplicada a um objeto. Extraia só quando o texto descreve um caso de uso concreto.
- Mecanismo: modalidade de apoio citada (subvenção, crédito reembolsável,
  investimento, bolsa). Modalidades de bolsa (mestrado, doutorado, iniciação
  científica) são Mecanismo — NUNCA Edital.
- Requisito: condição de elegibilidade citada (TRL, porte, receita, idade da
  empresa, região, certificação) ou prazo exigido
- Exclusão: entidade ou atividade explicitamente vedada pelo texto
- Entidade: TIPO DE ORGANIZAÇÃO elegível para aplicar (empresa, startup,
  microempresa, ICT). NÃO é papel nem pessoa (pesquisador, bolsista, diretoria).

Documentos, normas, portarias e sistemas internos (ex.: termo de outorga, portaria,
sistema de submissão) NÃO são entidades — ignore-os. Seja exaustivo no que importa;
cada entidade DEVE receber exatamente um destes tipos.

### TEXTO DE ORIGEM:
{source_text}
"""

_EDGE_PROMPT = """\
Você é um extrator de hiperarestas do ecossistema brasileiro de fomento. Extraia
relações que envolvem MÚLTIPLAS entidades simultaneamente (priorize 3+ membros).

REGRAS:
1. Uma hiperaresta conecta 2, 3 ou mais entidades.
2. Use SOMENTE entidades da lista "Entidades conhecidas" abaixo. Se algo não
   estiver na lista, exclua da aresta. Não invente participantes.
3. Cada aresta DEVE usar exatamente um destes tipos:
   - financia      : quem é financiado (Fonte/Edital → Entidade)
   - exige         : constraint de elegibilidade (Edital/Programa → Requisito)
   - abrange_tema  : cobertura temática (Edital/Programa → Tema/Tecnologia)
   - aplica_em     : como a tecnologia é usada (Tecnologia → Aplicação)
   - destina_a     : quem pode aplicar (Edital → Entidade)
   - exclui        : exclusão explícita (Edital → Exclusão)
   - parceria_com  : parceria obrigatória/oportunidade (Edital → ICT/Investidor)
   - pertence_a    : hierarquia (Edital → Programa → Fonte)
   - viabiliza     : capacidade habilita resultado (ICT/Tecnologia → Aplicação/Tema)
   - resolve       : ponte problema-solução (Tecnologia/Aplicação → Tema)

# Entidades conhecidas
{known_nodes}

# Texto de origem:
{source_text}
"""

# Variante para catálogos do ecossistema (ICT/Investidor/Programa) — NÃO são
# editais. Mesma taxonomia de 12 tipos, mas sem o enquadramento edital-cêntrico
# (que faria o LLM forçar nós Edital onde não há chamada).
_CATALOG_NODE_PROMPT = """\
Você está analisando um CATÁLOGO de atores do ecossistema brasileiro de fomento à
inovação — institutos de ciência e tecnologia (ICTs), investidores ou programas.
Cada bloco descreve um ator. Extraia TODAS as entidades MENCIONADAS NO TEXTO,
cada uma com seu tipo.

REGRA CRÍTICA: extraia SOMENTE entidades que aparecem no TEXTO. As definições de
tipo abaixo são descrições — NUNCA extraia um nome citado nelas.

TIPOS DE ENTIDADE (cada entidade recebe exatamente um):
- ICT: instituto, universidade ou centro de pesquisa (o ator do catálogo)
- Investidor: fundo de venture capital, CVC, FIP ou aceleradora (o ator)
- Programa: programa/iniciativa de fomento (o ator)
- Fonte: agência/operador financiador citado
- Tema: domínio de atuação (saúde, agronegócio, energia, manufatura 4.0…)
- Tecnologia: capacidade técnica específica citada (uma ferramenta/método)
- Aplicação: CASO DE USO concreto — uma OPERAÇÃO (triagem, classificação,
  diagnóstico, detecção, previsão, monitoramento, otimização) aplicada a um objeto
- Mecanismo: modalidade de apoio (subvenção, crédito, investimento/equity, bolsa)
- Requisito: condição de elegibilidade (estágio, ticket, setor, porte)
- Entidade: TIPO DE ORGANIZAÇÃO-alvo (startup, empresa, scale-up, microempresa)
- Exclusão: ator ou atividade explicitamente vedado
- Edital: NÃO se aplica a catálogo — só extraia se o texto citar uma chamada nominal

O ATOR de cada bloco (a ICT, o investidor ou o programa nomeado) é sempre um nó.
Ligue-o aos seus Temas/Tecnologias/Aplicações/Mecanismos. Cada entidade DEVE
receber exatamente um destes tipos.

### TEXTO DE ORIGEM:
{source_text}
"""

# Variante para o PERFIL DA EMPRESA (lado DEMANDA) — mesma taxonomia/embedder do
# ecossistema (o "canvas único" do match), enquadramento empresa-cêntrico. Extrai
# o que a empresa FAZ e BUSCA; nunca Edital/Fonte/ICT/Investidor/Programa (a
# empresa não é a oferta pública — evita alucinar oferta no lado demanda).
_COMPANY_NODE_PROMPT = """\
Você está analisando o PERFIL de UMA EMPRESA (ou startup/ICT) que busca
financiamento público à inovação. Extraia TODAS as entidades MENCIONADAS NO TEXTO,
cada uma com seu tipo — o que a empresa FAZ, DOMINA e BUSCA.

REGRA CRÍTICA: extraia SOMENTE entidades que aparecem no TEXTO. As definições de
tipo abaixo são descrições — NUNCA extraia um nome citado nelas.

TIPOS DE ENTIDADE (cada entidade recebe exatamente um):
- Entidade: a própria empresa/organização do perfil (o ator) e seu tipo (startup,
  empresa, microempresa, ICT)
- Tema: domínio em que a empresa atua (saúde, agronegócio, energia, manufatura 4.0…)
- Tecnologia: capacidade técnica que a empresa domina (ferramenta/método: IA, visão
  computacional, IoT, biotecnologia)
- Aplicação: CASO DE USO concreto da empresa — uma OPERAÇÃO (triagem, classificação,
  diagnóstico, detecção, previsão, monitoramento, otimização) aplicada a um objeto.
  É o tipo CENTRAL do match cross-domínio; extraia da proposta de valor, solução,
  portfólio e tração.
- Mecanismo: modalidade de financiamento que a empresa BUSCA (subvenção, crédito,
  investimento/equity, bolsa)
- Requisito: característica de elegibilidade que a empresa ATENDE (TRL, porte,
  faturamento, idade, UF/região)

NÃO extraia Edital, Fonte, Programa, ICT nem Investidor: a empresa é o lado DEMANDA,
não a oferta pública. Seja exaustivo no que a empresa faz e busca; cada entidade
DEVE receber exatamente um destes tipos.

### TEXTO DE ORIGEM:
{source_text}
"""


# ── clientes canônicos ────────────────────────────────────────────────────────

class _CanonicalEmbeddings:
    """Adapter LangChain `Embeddings` que delega ao embedder canônico do Radar.

    Garante que o hipergrado embeda no mesmo modelo/dim que `edital_chunks` —
    sem instanciar `langchain_openai.OpenAIEmbeddings`.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts)

    def embed_query(self, text: str) -> list[float]:
        return embed_query(text)


def _build_llm():
    """ChatOpenAI espelhando a config canônica de `make_client()` (timeout/retry/
    base_url) + envs do projeto. A lib exige um BaseChatModel LangChain, então não
    é possível passar o `OpenAI` cru de `make_client()`."""
    from langchain_openai import ChatOpenAI

    kw: dict = {
        "model": HYPER_EXTRACT_MODEL,
        "temperature": 0,
        "timeout": _llm_cfg.LLM_TIMEOUT_SECONDS,
        "max_retries": _llm_cfg.LLM_MAX_RETRIES,
    }
    base = _llm_cfg.LLM_BASE_URL or None
    key = os.environ.get("OPENAI_API_KEY")
    if base:
        kw["base_url"] = base
        key = key or "not-needed"
    if key:
        kw["api_key"] = key
    return ChatOpenAI(**kw)


# ── normalização de chaves (dedup case-insensitive) ───────────────────────────

def _norm(s: str) -> str:
    return s.lower().strip()


def _node_key(n: HyperNode) -> str:
    return _norm(n.name)


def _edge_members(e: HyperEdge) -> tuple[str, ...]:
    return tuple(_norm(m) for m in e.members)


def _edge_key(e: HyperEdge) -> str:
    return f"{e.type}_" + "_".join(sorted(_norm(m) for m in e.members))


def _build_extractor(node_prompt: str = _NODE_PROMPT):
    """Constrói um `AutoHypergraph` fresco com o schema de produção.

    `node_prompt` troca o enquadramento da extração de nós (edital vs catálogo);
    o schema de 12 tipos e o prompt de arestas são compartilhados."""
    from hyperextract import AutoHypergraph

    return AutoHypergraph(
        node_schema=HyperNode,
        edge_schema=HyperEdge,
        node_key_extractor=_node_key,
        edge_key_extractor=_edge_key,
        nodes_in_edge_extractor=_edge_members,
        llm_client=_build_llm(),
        embedder=_CanonicalEmbeddings(),
        prompt_for_node_extraction=node_prompt,
        prompt_for_edge_extraction=_EDGE_PROMPT,
        # Embeddings de match usam só semântica (name+description); prazo/status/
        # valor/fonte são display e poluiriam o vetor.
        node_fields_for_index=["name", "description"],
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        max_workers=_MAX_WORKERS,
        verbose=False,
    )


# ── I/O silver ────────────────────────────────────────────────────────────────

def _deaccent(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)
    )


def _doc_skip_keywords(source: str) -> list[str]:
    """Skip-list autoritativa de docs-formulário de `wikis/<fonte>.md`, deaccentuada.

    Reusa a fonte única `wiki_schema.skip_keywords` (declaração/modelo/carta/
    apresentação/orientações…) mas aplica com dobra de acento — a lista é sem
    acento e os nomes de arquivo são acentuados, então o match exato do adapter
    nunca casa (bug latente que também vaza pro RAG/descoberta).
    TODO migração: quando `wiki_schema` for removido, relocar este reader; os
    keywords vivem em `wikis/<fonte>.md` e sobrevivem."""
    try:
        from core.kg import wiki_schema
        kws = wiki_schema.skip_keywords(source) or []
    except Exception:  # noqa: BLE001 — fonte indisponível → sem skip (degrada ok)
        kws = []
    return [_deaccent(k) for k in kws]


def load_silver(path: Path, max_chars: int = HYPER_EXTRACT_MAX_CHARS) -> tuple[str, str]:
    """Reconstrói `(título, texto)` de um silver JSONL.

    `título` = heading "title-like" (ver _TITLE_START_RE). `texto` = blocos
    concatenados, descartando: (a) `kind ∈ {boilerplate, signature}`
    (navegação/rodapé) e (b) docs-formulário inteiros via skip-list autoritativa
    (declaração/modelo/carta/apresentação). Sem cap por default (`max_chars=0`)."""
    path = Path(path)
    skip = _doc_skip_keywords(path.parent.name)
    headings: list[str] = []
    blocks: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            txt = (rec.get("text") or "").strip()
            if not txt:
                continue
            # Dropa docs-formulário inteiros (não acrescentam ao match; só ruído).
            if skip and any(k in _deaccent(rec.get("doc", "")) for k in skip):
                continue
            if rec.get("kind") == "heading":
                headings.append(txt)
            if rec.get("kind") in _DROP_KINDS:
                continue
            blocks.append(txt)
    # Título: prefere o heading mais longo que COMEÇA com palavra-chave de título
    # (sinal forte); senão o mais longo que contém uma; senão 1º heading / 1º bloco.
    # Candidatos a título: headings que NÃO começam com número de seção —
    # "4.3. PRIMEIRA ETAPA…" / "8.1. Será destinado…" são cláusulas mal-tagueadas
    # como heading, não o título da chamada.
    cands = [h for h in headings if not re.match(r"^\s*\d", h)]
    starts = [h for h in cands if _TITLE_START_RE.match(h)]
    contains = [h for h in cands if _TITLE_RE.search(h)]
    if starts:
        title = max(starts, key=len)
    elif contains:
        title = max(contains, key=len)
    elif headings:
        title = headings[0]
    elif blocks:
        title = blocks[0]
    else:
        title = ""
    text = "\n".join(blocks)
    return title, (text[:max_chars] if max_chars else text)


def _collapse_editais(
    nodes: list[dict], edges: list[dict], *, title: str, edital_id: str
) -> tuple[list[dict], list[dict]]:
    """Funde todos os nós `Edital` num único nó canônico (1 arquivo = 1 edital).

    A extração chunk-a-chunk fragmenta o mesmo edital em vários nós ("Chamada",
    "Chamada Pública", título completo…), com prazo/status/valor espalhados. Como
    o arquivo silver é exatamente um edital, colapsamos:
      * nome = `title` (H1 do silver) — a identidade canônica
      * prazo/status/valor/fonte = primeiro valor não-nulo entre os fragmentos
      * membros das arestas que referenciavam qualquer fragmento → nome canônico
    Arestas que degeneram (<2 membros distintos) ou duplicam são removidas."""
    edital_nodes = [n for n in nodes if n.get("type") == "Edital"]
    if not edital_nodes:
        return nodes, edges
    others = [n for n in nodes if n.get("type") != "Edital"]

    def _first(prop: str):
        for n in edital_nodes:
            if n.get(prop):
                return n[prop]
        return None

    # Nome canônico = título do silver (já escolhido em load_silver como o heading
    # mais "title-like", robusto a navegação web). Fragmentos do LLM só como
    # fallback — eles trazem lixo verboso ("8.1. Será destinado…") que, contendo
    # "Edital", venceria o heading limpo se entrasse no pool.
    canon_name = (title or "").strip()
    if not canon_name:
        frag_like = [n["name"] for n in edital_nodes if _TITLE_RE.search(n["name"])]
        pool = frag_like or [n["name"] for n in edital_nodes]
        canon_name = max(pool, key=len) if pool else f"Edital {edital_id}"
    canon = {
        "name": canon_name,
        "type": "Edital",
        "description": _first("description") or "",
        "prazo": _first("prazo"),
        "status": _first("status"),
        "valor": _first("valor"),
        "fonte": _first("fonte"),
        "edital_id": edital_id,
    }
    frag_keys = {_norm(n["name"]) for n in edital_nodes}

    new_edges: list[dict] = []
    seen: set = set()
    for e in edges:
        members = [
            canon_name if _norm(m) in frag_keys else m for m in e.get("members", [])
        ]
        members = list(dict.fromkeys(members))  # dedup preservando ordem
        if len(members) < 2:
            continue  # aresta degenerada após colapso
        key = (e["type"], tuple(sorted(_norm(m) for m in members)))
        if key in seen:
            continue
        seen.add(key)
        new_edges.append({**e, "members": members})

    return [canon, *others], new_edges


# ── resultado ─────────────────────────────────────────────────────────────────

@dataclass
class HypergraphResult:
    edital_id: str
    path: Path | None
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    skipped: bool = False  # True = input inalterado (hash bateu), não re-extraído

    @property
    def node_type_counts(self) -> Counter:
        return Counter(n.get("type", "?") for n in self.nodes)

    @property
    def edge_type_counts(self) -> Counter:
        return Counter(e.get("type", "?") for e in self.edges)

    @property
    def editais(self) -> list[dict]:
        """Nós do tipo Edital (carregam as propriedades de display)."""
        return [n for n in self.nodes if n.get("type") == "Edital"]


# ── cache por hash do input ───────────────────────────────────────────────────
# Skip do ETL diário: só re-extrai quando o texto-fonte muda. O hash vai gravado
# no próprio JSON (`source_hash`). Mudou prompt/schema? delete hypergraphs/ para
# forçar (mesmo padrão do .enrichment_cache.json).

def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_current(out_path: Path, source_hash: str) -> bool:
    """True se já há um hipergrado gravado com o mesmo hash de input."""
    if not out_path.exists():
        return False
    try:
        return json.loads(out_path.read_text(encoding="utf-8")).get("source_hash") == source_hash
    except Exception:  # noqa: BLE001 — JSON corrompido → re-extrai
        return False


def _write_hypergraph(out_path: Path, source_hash: str, nodes: list, edges: list) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"source_hash": source_hash, "nodes": nodes, "edges": edges},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


# ── API pública ───────────────────────────────────────────────────────────────

def run_hyper_extract(
    silver_jsonl_path: Path,
    edital_id: str,
    *,
    source: str = "",
    out_dir: Path = HYPERGRAPHS_DIR,
    build_index: bool = False,
    write: bool = True,
    skip_if_unchanged: bool = False,
) -> HypergraphResult:
    """Extrai o hipergrado de UM edital a partir do seu silver JSONL.

    Lê `silver_jsonl_path` → texto plano → Hyper-Extract (schema de produção) e,
    se `write`, grava `out_dir/{source}__{edital_id}.json` (`{source_hash, nodes,
    edges}`). O prefixo de `source` evita colisão de id nativo entre fontes
    (ex.: FINEP e FAPESP com o mesmo número) — o id do nó Edital segue nativo.

    `skip_if_unchanged=True` (ETL): pula sem chamar o LLM se já existe um
    hipergrado com o mesmo hash de input. `build_index=True` constrói o índice
    vetorial (Sprint 1). Retorna sempre um `HypergraphResult`.
    """
    silver_jsonl_path = Path(silver_jsonl_path)
    title, text = load_silver(silver_jsonl_path)
    if not text.strip():
        logger.warning("hyper_extract: silver vazio edital_id=%s", edital_id)
        return HypergraphResult(edital_id=edital_id, path=None)

    source_hash = _text_hash(text)
    file_key = f"{source}__{edital_id}" if source else edital_id
    out_path = out_dir / f"{file_key}.json"
    if skip_if_unchanged and write and _is_current(out_path, source_hash):
        logger.info("hyper_extract: edital_id=%s inalterado (hash) — skip", edital_id)
        return HypergraphResult(edital_id=edital_id, path=out_path, skipped=True)

    hg = _build_extractor()
    hg.feed_text(text)
    if build_index:
        hg.build_index()

    data = hg.data
    nodes = [n.model_dump() for n in data.nodes]
    edges = [e.model_dump() for e in data.edges]
    # Colapsa os fragmentos de Edital num nó canônico (1 arquivo = 1 edital).
    nodes, edges = _collapse_editais(nodes, edges, title=title, edital_id=edital_id)

    written: Path | None = None
    if write:
        _write_hypergraph(out_path, source_hash, nodes, edges)
        written = out_path
        logger.info(
            "hyper_extract: edital_id=%s nós=%d arestas=%d → %s",
            edital_id, len(nodes), len(edges), out_path,
        )

    return HypergraphResult(edital_id=edital_id, path=written, nodes=nodes, edges=edges)


def iter_silver_editais(structured_docs_dir: Path = STRUCTURED_DOCS_DIR):
    """Itera `(source, edital_id, silver_path)` sobre todos os editais silver.

    Cada `{edital_id}.jsonl` em `structured_docs/{source}/` é UM edital."""
    for source_dir in sorted(p for p in structured_docs_dir.iterdir() if p.is_dir()):
        for jsonl in sorted(source_dir.glob("*.jsonl")):
            yield source_dir.name, jsonl.stem, jsonl


# ── catálogos do ecossistema (ICT / Investidor / Programa) ────────────────────
# Não são editais (sem silver, sem colapso). O texto vem dos CAMPOS CURADOS dos
# catálogos (tese/temas/setores/elegibilidade) — mais rico e determinístico que
# raspar URLs (o que o experimento fazia). Mitiga o risco "Investidor ralo".
# SEM cap por default (Opção A): o cap por-edital é a unidade errada p/ um
# catálogo de N itens independentes — capar a 60k descartava 71 dos 90 ICTs.
# A lib chunka internamente (2048), então passar o texto inteiro cobre todos.

def _fmt(v) -> str:
    """Formata um campo do catálogo (lista → CSV; resto → str)."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v if x)
    return str(v)


def _block(label: str, fields: list[tuple[str, object]]) -> str:
    """Monta um bloco narrativo `Label: nome` + linhas `Campo: valor` não-vazias."""
    lines = [label]
    for name, val in fields:
        s = _fmt(val).strip()
        if s:
            lines.append(f"{name}: {s}")
    return "\n".join(lines)


def load_ict_text(max_chars: int | None = None) -> str:
    """Texto narrativo do catálogo de ICTs (bronze/ict_raw, JSON mais recente)."""
    files = sorted((BRONZE_DIR / "ict_raw").glob("*.json"))
    if not files:
        return ""
    items = json.loads(files[-1].read_text(encoding="utf-8"))
    blocks = [
        _block(
            f"ICT: {it.get('name', '')}",
            [
                ("Tipo", it.get("institution_type")),
                ("Áreas", it.get("areas_raw")),
                ("Sobre", it.get("about")),
            ],
        )
        for it in items
        if it.get("name") or it.get("about")
    ]
    text = "\n\n---\n\n".join(blocks)
    return text[:max_chars] if max_chars else text


def load_investidores_text(max_chars: int | None = None) -> str:
    """Texto narrativo do catálogo de investidores (campos curados, sem scraping)."""
    path = KNOWLEDGE_GRAPH_DIR / "investidores.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    blocks = [
        _block(
            f"Investidor: {it.get('name', '')}",
            [
                ("Tese", it.get("tese")),
                ("Temas", it.get("tese_themes")),
                ("Setores", it.get("setores")),
                ("Estágio-alvo", it.get("estagio_alvo")),
                ("Ticket", it.get("ticket_range")),
            ],
        )
        for it in data.get("investidores", [])
        if it.get("name")
    ]
    text = "\n\n---\n\n".join(blocks)
    return text[:max_chars] if max_chars else text


def load_programas_text(max_chars: int | None = None) -> str:
    """Texto narrativo do catálogo de programas (campos curados, sem scraping)."""
    path = KNOWLEDGE_GRAPH_DIR / "programas.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    blocks = [
        _block(
            f"Programa: {it.get('name', '')}",
            [
                ("Operador", it.get("operador")),
                ("Tipo", it.get("tipo")),
                ("Descrição", it.get("descricao")),
                ("Benefício", it.get("beneficio")),
                ("Temas", it.get("tese_themes")),
                ("Setores", it.get("setores")),
                ("Estágio-alvo", it.get("estagio_alvo")),
                ("Elegibilidade", it.get("elegibilidade")),
            ],
        )
        for it in data.get("programas", [])
        if it.get("name")
    ]
    text = "\n\n---\n\n".join(blocks)
    return text[:max_chars] if max_chars else text


# (graph_id, loader). Cada catálogo → hypergraphs/{graph_id}.json.
CATALOG_LOADERS = {
    "ict": load_ict_text,
    "investidores": load_investidores_text,
    "programas": load_programas_text,
}


def run_hyper_extract_catalog(
    text: str,
    graph_id: str,
    *,
    out_dir: Path = HYPERGRAPHS_DIR,
    build_index: bool = False,
    write: bool = True,
    skip_if_unchanged: bool = False,
) -> HypergraphResult:
    """Extrai o hipergrado de um catálogo de ecossistema (ICT/investidor/programa).

    Diferente de `run_hyper_extract`: usa o prompt edital-neutro e NÃO colapsa
    (catálogos não têm fragmentação de Edital). Grava `out_dir/{graph_id}.json`."""
    if not text.strip():
        logger.warning("hyper_extract_catalog: texto vazio graph_id=%s", graph_id)
        return HypergraphResult(edital_id=graph_id, path=None)

    source_hash = _text_hash(text)
    out_path = out_dir / f"{graph_id}.json"
    if skip_if_unchanged and write and _is_current(out_path, source_hash):
        logger.info("hyper_extract_catalog: graph_id=%s inalterado (hash) — skip", graph_id)
        return HypergraphResult(edital_id=graph_id, path=out_path, skipped=True)

    hg = _build_extractor(node_prompt=_CATALOG_NODE_PROMPT)
    hg.feed_text(text)
    if build_index:
        hg.build_index()

    data = hg.data
    nodes = [n.model_dump() for n in data.nodes]
    edges = [e.model_dump() for e in data.edges]

    written: Path | None = None
    if write:
        _write_hypergraph(out_path, source_hash, nodes, edges)
        written = out_path
        logger.info(
            "hyper_extract_catalog: graph_id=%s nós=%d arestas=%d → %s",
            graph_id, len(nodes), len(edges), out_path,
        )

    return HypergraphResult(edital_id=graph_id, path=written, nodes=nodes, edges=edges)


def run_hyper_extract_company(
    text: str,
    *,
    company_id: str = "company",
    build_index: bool = False,
) -> HypergraphResult:
    """Extrai o hipergrado do PERFIL DA EMPRESA (lado DEMANDA) — MESMO schema e
    embedder do ecossistema (o canvas único do match). NÃO grava no KG global: o
    grafo da empresa é privado/efêmero (por workspace), computado on-demand para o
    match e descartado. Sem colapso (não é Edital). Retorna nodes/edges em memória."""
    if not text.strip():
        logger.warning("hyper_extract_company: perfil vazio company_id=%s", company_id)
        return HypergraphResult(edital_id=company_id, path=None)

    hg = _build_extractor(node_prompt=_COMPANY_NODE_PROMPT)
    hg.feed_text(text)
    if build_index:
        hg.build_index()

    data = hg.data
    nodes = [n.model_dump() for n in data.nodes]
    edges = [e.model_dump() for e in data.edges]
    logger.info(
        "hyper_extract_company: company_id=%s nós=%d arestas=%d",
        company_id, len(nodes), len(edges),
    )
    return HypergraphResult(edital_id=company_id, path=None, nodes=nodes, edges=edges)


def iter_catalogs():
    """Itera `(graph_id, text)` sobre os catálogos de ecossistema não-vazios."""
    for graph_id, loader in CATALOG_LOADERS.items():
        text = loader()
        if text.strip():
            yield graph_id, text


def _publish_hypergraphs(out_dir: Path = HYPERGRAPHS_DIR) -> int:
    """Publica os subgrafos do disco no storage durável (PG) — o FS do worker no
    Railway é EFÊMERO. Lê de `out_dir` (inclui os pulados por cache, que não
    retornam nodes) e delega a `kg_store.save_hypergraphs` (no-op em modo file
    puro, sem Supabase). Retorna nº de subgrafos publicados."""
    from core.kg.kg_store import save_hypergraphs  # lazy: não acopla no import-time
    graphs: dict[str, dict] = {}
    for p in sorted(out_dir.glob("*.json")):
        try:
            graphs[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — JSON corrompido não derruba o ETL
            logger.warning("publish_hypergraphs: JSON inválido %s", p)
    save_hypergraphs(graphs)
    return len(graphs)


def build_all_hypergraphs(
    *,
    skip_if_unchanged: bool = True,
    exclude_sources: frozenset = frozenset({"web"}),
) -> dict[str, int]:
    """Constrói/atualiza todos os hipergrados (editais + catálogos) — passo do ETL.

    Lê o silver existente (produzido pela ingestão) + os catálogos curados, e só
    re-extrai o que mudou (skip por hash). `web` (staging Discovery) fica fora.
    Retorna `{built, skipped, failed}`. Falha de um edital não derruba os demais.
    """
    built = skipped = failed = 0
    for source, edital_id, path in iter_silver_editais():
        if source in exclude_sources:
            continue
        try:
            res = run_hyper_extract(path, edital_id, source=source, skip_if_unchanged=skip_if_unchanged)
            skipped += res.skipped
            built += not res.skipped
        except Exception:
            logger.exception("build_all_hypergraphs: falha edital=%s", edital_id)
            failed += 1
    for graph_id, text in iter_catalogs():
        try:
            res = run_hyper_extract_catalog(text, graph_id, skip_if_unchanged=skip_if_unchanged)
            skipped += res.skipped
            built += not res.skipped
        except Exception:
            logger.exception("build_all_hypergraphs: falha catálogo=%s", graph_id)
            failed += 1
    published = _publish_hypergraphs()  # durável p/ prod (no-op em modo file)
    return {"built": built, "skipped": skipped, "failed": failed, "published": published}
