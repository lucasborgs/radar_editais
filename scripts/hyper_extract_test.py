#!/usr/bin/env python3
"""
Teste controlado: extração de Hypergraph do corpus Radar de Editais via Hyper-Extract.

Corpus  : editais FINEP + FAPESP + FAPESC (silver JSONL) + ICTs + Investidores + Programas
LLM     : gpt-4o-mini
Embedder: text-embedding-3-small
Saída   : data/hyper_extract_output_v2/  (dump JSON + vault Obsidian)

Uso:
    .venv/bin/python3.14 scripts/hyper_extract_test.py
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import List, Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field

load_dotenv()

ROOT = Path(__file__).parent.parent
SILVER = ROOT / "data/silver/structured_docs"
OUTPUT = ROOT / "data/hyper_extract_output_v2"
MAX_CHARS = 20_000  # por doc — cost control (gpt-4o-mini ~$0.01 total)

EDITAL_DIRS = [
    SILVER / "finep",
    SILVER / "fapesp",
    SILVER / "fapesc",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def load_silver(path: Path, max_chars: int = MAX_CHARS) -> tuple[str, str]:
    """Reconstrói (nome, texto) de um arquivo silver JSONL."""
    lines = [json.loads(l) for l in open(path) if l.strip()]
    if not lines:
        return path.stem, ""
    name = lines[0].get("doc", path.stem)
    text = "\n".join(l["text"] for l in lines if l.get("text", "").strip())
    return name, text[:max_chars]


def load_icts(max_chars: int = 20_000) -> str:
    """Converte bronze ICT JSON em texto narrativo concatenado."""
    import glob
    files = glob.glob(str(ROOT / "data/bronze/ict_raw/*.json"))
    if not files:
        return ""
    items = json.load(open(sorted(files)[-1]))  # mais recente
    blocks = []
    for it in items:
        name = it.get("name", "")
        about = it.get("about", "")
        if name or about:
            blocks.append(f"ICT: {name}\n{about}")
    return "\n\n---\n\n".join(blocks)[:max_chars]


def _scrape_urls(urls: list[str], max_per_url: int = 8_000) -> str:
    """Raspa lista de URLs via fetch_and_parse e concatena o texto limpo."""
    from core.web.fetch import fetch_and_parse
    blocks = []
    for url in urls:
        if not url:
            continue
        try:
            result = fetch_and_parse(url)
            text = (result.get("text") or "").strip()
            title = result.get("title") or url
            if text:
                blocks.append(f"### {title}\n{text[:max_per_url]}")
        except Exception as e:
            print(f"    [WARN] {url}: {e}")
    return "\n\n".join(blocks)


def load_investidores(max_chars: int = 20_000) -> str:
    """Raspa source_urls de cada investidor e concatena o texto."""
    path = ROOT / "data/knowledge_graph/investidores.json"
    if not path.exists():
        return ""
    data = json.load(open(path))
    sections = []
    for it in data.get("investidores", []):
        name = it.get("name", "")
        urls = list(dict.fromkeys(filter(None, [
            it.get("site"),
            *it.get("source_urls", []),
        ])))
        print(f"    {name} ({len(urls)} URLs)")
        scraped = _scrape_urls(urls)
        if scraped:
            sections.append(f"## Investidor: {name}\n{scraped}")
    return "\n\n===\n\n".join(sections)[:max_chars]


def load_programas(max_chars: int = 20_000) -> str:
    """Raspa source_urls de cada programa e concatena o texto."""
    path = ROOT / "data/knowledge_graph/programas.json"
    if not path.exists():
        return ""
    data = json.load(open(path))
    sections = []
    for it in data.get("programas", []):
        name = it.get("name", "")
        urls = list(dict.fromkeys(filter(None, [
            it.get("site"),
            it.get("faq_url"),
            *it.get("source_urls", []),
        ])))
        print(f"    {name} ({len(urls)} URLs)")
        scraped = _scrape_urls(urls)
        if scraped:
            sections.append(f"## Programa: {name}\n{scraped}")
    return "\n\n===\n\n".join(sections)[:max_chars]


# ── schema de domínio (editais brasileiros) ───────────────────────────────────

class Entity(BaseModel):
    name: str = Field(description="Nome da entidade")
    type: Literal[
        "Edital",       # chamada pública de fomento
        "Desafio",      # challenge/grand challenge orientado a problema
        "Programa",     # programa de fomento que agrupa editais
        "Subprograma",  # subdivisão de Programa
        "Fonte",        # órgão financiador (FINEP, FAPESP, FAPESC, EMBRAPII…)
        "ICT",          # instituição de ciência e tecnologia parceira
        "Investidor",   # fundo/investidor equity
        "Tema",         # área temática ampla
        "Tecnologia",   # objeto técnico específico mais granular que Tema
        "Mecanismo",    # modalidade de apoio (subvenção, crédito, bolsa, equity)
        "Requisito",    # condição de elegibilidade exigida
        "Exclusão",     # entidade/atividade explicitamente vedada
        "Região",       # escopo geográfico
        "Empresa",      # empresa/startup (proponente ou elegível)
        "Outro",        # fallback
    ] = Field(description="Categoria da entidade no ecossistema de fomento")
    description: str = Field(description="Descrição breve no contexto do edital")


class HyperEdge(BaseModel):
    type: str = Field(
        description=(
            "Tipo da relação. Exemplos: financia, exige, abrange_tema, "
            "aplica_para, seleciona_via, vigencia_em, parceria_com, destina_a"
        )
    )
    members: List[str] = Field(
        description="Nomes de todas as entidades participantes desta relação (mínimo 2)"
    )
    description: str = Field(description="Descrição da relação hypergráfica")


DOMAIN_PROMPT = """\
Você está analisando o ecossistema brasileiro de fomento à pesquisa e inovação.
O corpus inclui editais de agências (FINEP, FAPESP, FAPESC), instituições de
ciência e tecnologia (ICTs), programas de fomento e investidores de equity.

ENTIDADES A EXTRAIR:
- Edital / Desafio: chamadas públicas de financiamento (nome + número)
- Programa / Subprograma: iniciativas que agrupam editais
- Fonte: órgão financiador (FINEP, FAPESP, FAPESC, EMBRAPII, CNPq…)
- ICT: institutos, universidades, centros de pesquisa parceiros
- Investidor: fundos de venture capital, CVC, aceleradoras
- Tema: área temática ampla (bioeconomia, saúde, agtech, energia…)
- Tecnologia: objeto técnico específico (sensores, CRISPR, blockchain…)
- Mecanismo: modalidade de apoio (subvenção, crédito reembolsável, bolsa, equity)
- Requisito: condição de elegibilidade (TRL mínimo, porte, receita, certificação)
- Exclusão: entidade ou atividade explicitamente vedada
- Região: escopo geográfico (estado, região, município)
- Empresa: empresa, startup ou tipo de proponente elegível

RELAÇÕES HIPERGRÁFICAS (priorize 3+ participantes):
- financia       : [Fonte, Programa, Empresa] ou [Fonte, Edital, ICT]
- exige          : [Edital, Requisito, Empresa] — condições de elegibilidade
- exclui         : [Edital, Exclusão] — o que não é financiável
- abrange_tema   : [Edital, Tema, Tecnologia] — escopo temático
- apoia_via      : [Programa, Mecanismo, Valor, Empresa]
- investe_em     : [Investidor, Empresa, Tema, Estágio]
- parceria       : [Empresa, ICT, Programa] — co-execução obrigatória ou preferencial
- destina_a      : [Edital, Região, Empresa] — escopo geográfico e público
- seleciona_via  : [Edital, Critério, Fase] — processo seletivo
- conecta        : [ICT, Tema, Programa] — competência de ICT ligada a programa

Priorize hiperedges com 3 ou mais participantes quando o texto permitir.
"""


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    from hyperextract.types import AutoHypergraph

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY não encontrada no ambiente.")

    OUTPUT.mkdir(parents=True, exist_ok=True)

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    embedder = OpenAIEmbeddings(model="text-embedding-3-small")

    hg: AutoHypergraph[Entity, HyperEdge] = AutoHypergraph(
        node_schema=Entity,
        edge_schema=HyperEdge,
        node_key_extractor=lambda x: x.name,
        # sort members para evitar duplicatas {A,B} vs {B,A}
        edge_key_extractor=lambda x: f"{x.type}_{'_'.join(sorted(x.members))}",
        nodes_in_edge_extractor=lambda x: tuple(x.members),
        llm_client=llm,
        embedder=embedder,
        prompt=DOMAIN_PROMPT,
        chunk_size=2048,
        chunk_overlap=256,
        max_workers=4,
        verbose=True,
    )

    # ── ingestão ──────────────────────────────────────────────────────────────
    fed_docs = []

    # ── editais ───────────────────────────────────────────────────────────────
    print("\n=== EDITAIS ===")
    for source_dir in EDITAL_DIRS:
        if not source_dir.exists():
            continue
        jsonls = sorted(source_dir.glob("*.jsonl"))
        print(f"\n[{source_dir.name.upper()}] {len(jsonls)} docs")
        for doc_path in jsonls:
            name, text = load_silver(doc_path)
            if not text.strip():
                continue
            print(f"  FEED {name[:60]} ({len(text):,} chars)")
            hg.feed_text(text)
            fed_docs.append(f"{source_dir.name}/{name}")

    # ── ICTs ──────────────────────────────────────────────────────────────────
    print("\n=== ICTs ===")
    ict_text = load_icts()
    if ict_text:
        print(f"  FEED ICTs ({len(ict_text):,} chars)")
        hg.feed_text(ict_text)
        fed_docs.append("ict/embrapii")
    else:
        print("  [SKIP] nenhum arquivo ICT encontrado")

    # ── Investidores ──────────────────────────────────────────────────────────
    print("\n=== INVESTIDORES ===")
    inv_text = load_investidores()
    if inv_text:
        print(f"  FEED Investidores ({len(inv_text):,} chars)")
        hg.feed_text(inv_text)
        fed_docs.append("investidores")
    else:
        print("  [SKIP] investidores.json não encontrado")

    # ── Programas ─────────────────────────────────────────────────────────────
    print("\n=== PROGRAMAS ===")
    prog_text = load_programas()
    if prog_text:
        print(f"  FEED Programas ({len(prog_text):,} chars)")
        hg.feed_text(prog_text)
        fed_docs.append("programas")
    else:
        print("  [SKIP] programas.json não encontrado")

    if not fed_docs:
        sys.exit("Nenhum documento ingerido.")

    # ── indexação ─────────────────────────────────────────────────────────────
    print("\n=== INDEXAÇÃO ===")
    hg.build_index()

    # ── persistência ─────────────────────────────────────────────────────────
    dump_path = OUTPUT / "dump"
    print(f"\n=== DUMP → {dump_path} ===")
    hg.dump(dump_path)

    # ── export Obsidian ───────────────────────────────────────────────────────
    vault_path = OUTPUT / "vault"
    print(f"\n=== OBSIDIAN → {vault_path} ===")
    hg.export_obsidian(
        vault_path,
        node_label_extractor=lambda x: x.name,
        edge_label_extractor=lambda x: x.type,
        vault_name="Radar Editais KG",
        include_index=True,
        overwrite=True,
    )

    # ── estatísticas ─────────────────────────────────────────────────────────
    print("\n=== ESTATÍSTICAS ===")
    data = hg.data
    raw_nodes = getattr(data, "nodes", [])
    raw_edges = getattr(data, "edges", [])
    nodes = list(raw_nodes.values()) if isinstance(raw_nodes, dict) else list(raw_nodes)
    edges = list(raw_edges.values()) if isinstance(raw_edges, dict) else list(raw_edges)

    print(f"Documentos ingeridos : {len(fed_docs)}")
    print(f"Nós (entidades)      : {len(nodes)}")
    print(f"Arestas (hyperedges) : {len(edges)}")

    if nodes:
        type_counts = Counter(getattr(n, "type", "?") for n in nodes)
        print(f"\nTipos de nó:")
        for t, c in type_counts.most_common():
            print(f"  {t:15s} {c:3d}")

    if edges:
        edge_counts = Counter(getattr(e, "type", "?") for e in edges)
        print(f"\nTipos de aresta:")
        for t, c in edge_counts.most_common():
            print(f"  {t:20s} {c:3d}")

        # hyperedges com 3+ membros
        multi = [e for e in edges if len(getattr(e, "members", [])) >= 3]
        print(f"\nHyperedges com 3+ membros: {len(multi)}")
        for e in multi[:5]:
            print(f"  [{e.type}] {' + '.join(e.members)}")

    # ── JSON summary ─────────────────────────────────────────────────────────
    summary = {
        "docs": fed_docs,
        "nodes": [n.model_dump() for n in nodes],
        "edges": [e.model_dump() for e in edges],
    }
    summary_path = OUTPUT / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nSummary JSON  : {summary_path}")
    print(f"Obsidian vault: {vault_path}")
    print(f"Dump raw      : {dump_path}")

    # ── visualização interativa ───────────────────────────────────────────────
    print("\n=== VISUALIZAÇÃO (OntoSight) ===")
    print("Abrindo grafo interativo… (ctrl+C para sair)")
    try:
        hg.show(
            node_label_extractor=lambda x: x.name,
            edge_label_extractor=lambda x: x.type,
        )
    except KeyboardInterrupt:
        print("Visualização encerrada.")
    except Exception as e:
        print(f"[WARN] show() falhou: {e}")
        print("Use: he show", dump_path)


if __name__ == "__main__":
    main()
