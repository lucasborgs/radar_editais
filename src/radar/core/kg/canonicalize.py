"""core/kg/canonicalize.py — filtro determinístico anti-classe de tags.

Resíduo vivo da higiene de Conceitos do KG v2: os passes LLM/grafo (validação,
merges, macro-temas, canonicalize_graph) morreram com o hipergrado (PR-C v3). O
que fica é `anti_class_verdict` — descarte determinístico, zero LLM, consumido por
`radar.core.kg.gold.normalize_tags` para tirar métrica/faceta (TRL), citação legal (LGPD,
"Lei nº X") e rótulo genérico nu ("programa", "tecnologia") das tags de tecnologia
antes de gravá-las em `entities.tecnologias_tags`.
"""
from __future__ import annotations

import re
import unicodedata


def _deburr(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


# Faceta/métrica de maturidade (capturada como propriedade `trl`, nunca tema).
_METRICA_RE = re.compile(
    r"\b(trl|mrl|crl|irl)\b|technology readiness|n[íi]vel de maturidade tecnol[óo]g",
    re.IGNORECASE,
)
# Citação legal específica (LGPD, "Lei nº X", decreto/portaria numerados, marco civil).
# A parte numerada não leva \b final: o \d costuma ser seguido de outro dígito
# ("14.133"), e \b ali quebraria o match. O separador entre a palavra-lei e o
# número é uma classe explícita — só "nº/n°/no/n." + espaços/pontos/hífen (os
# ordinais ª/º são LETRA em Unicode, então \W não os cobre); "Lei do Bem" (o "d"
# não é separador) NÃO casa, de propósito (é mecanismo, não citação).
_LEGAL_RE = re.compile(
    r"\b(lgpd|lei geral de prote[çc][ãa]o de dados|marco civil)\b"
    r"|\b(lei(\s+complementar)?|decreto(-lei)?|portaria|medida provis[óo]ria"
    r"|emenda constitucional)\b[\sºª°no.\-]*\d",
    re.IGNORECASE,
)
# Rótulos genéricos: só a forma NUA (name == rótulo, após deburr/lower) é lixo.
# Expandido em 2026-07-07 para cobrir stop-words temáticas que aparecem em
# praticamente todo edital de fomento e não discriminam domínio.
_GENERIC_LABELS = frozenset({
    # Originais
    "programa", "programas", "tecnologia", "tecnologias", "consultoria",
    "inovacao", "projeto", "projetos", "pesquisa", "desenvolvimento",
    "p&d", "pd&i", "p&d&i", "pdi", "empresa", "empresas", "startup", "startups",
    "servico", "servicos", "produto", "produtos", "solucao", "solucoes",
    "sistema", "sistemas", "plataforma", "metodologia", "processo",
    # Prototipagem / fabricação / engenharia
    "prototipagem", "prototipo", "prototipos",
    "benchmark", "ensaio", "teste", "testes",
    "calibracao", "validacao", "homologacao",
    # Escala / abrangência
    "escala", "porte",
    # Sustentabilidade / gestão (bare)
    "sustentabilidade", "viabilidade",
    "gestao", "governanca",
    # Fomento / financiamento (bare)
    "fomento", "incentivo", "subvencao", "financiamento", "credito",
    "investimento", "capital", "recurso", "recursos",
    # Infraestrutura / recursos (bare)
    "infraestrutura", "laboratorio", "laboratorios",
    "equipamento", "equipamentos",
    "ferramenta", "ferramentas", "dispositivo", "dispositivos",
    "instrumento", "instrumentos", "sensor", "sensores",
    "modulo", "unidade",
    # Genérico / transversal
    "concepcao", "modelagem", "simulacao", "otimizacao",
    "caracterizacao", "parametrizacao", "padronizacao",
    "monitoramento", "mensuracao",
    "rastreamento", "rastreabilidade",
    "inspecao", "normatizacao", "certificacao",
    "afericao", "medicao", "quantificacao", "qualificacao",
    # Mercado / negócio (bare)
    "mercado", "negocio", "negocios",
    "cliente", "clientes", "demanda", "demandas",
    "desafio", "desafios", "tendencia",
    # Suporte / capacitação (bare)
    "suporte", "assessoria", "apoio", "assistencia",
    "capacitacao", "treinamento", "formacao",
    "divulgacao", "comunicacao", "difusao",
    "transferencia", "absorcao", "adocao",
    "implantacao", "implementacao",
    "execucao", "realizacao", "coordenacao",
    # Tempo / planejamento
    "fase", "fases", "etapa", "etapas",
    "prazo", "cronograma", "vigencia",
    # Qualidade / avaliação (bare)
    "qualidade", "eficiencia", "eficacia", "efetividade",
    "desempenho", "produtividade", "competitividade",
    "fundamento", "principio", "principios",
    "diretriz", "diretrizes",
    "norma", "normas", "regra", "regras",
    "criterio", "criterios",
    "premissa", "premissas",
})


def anti_class_verdict(name: str) -> dict | None:
    """Descarte determinístico de tag por classe errada: métrica/faceta (TRL),
    citação legal (LGPD, "Lei nº X"), rótulo genérico nu ("programa", "tecnologia",
    "prototipagem", "sustentabilidade"). Retorna {veredicto, categoria} para
    descartar, ou None para manter. Casa só o inequívoco; composto legítimo
    ("tecnologia assistiva", "saúde digital") passa incólume."""
    n = (name or "").strip()
    if not n:
        return None
    if _METRICA_RE.search(n):
        return {"veredicto": "descartar", "categoria": "metrica"}
    if _LEGAL_RE.search(n):
        return {"veredicto": "descartar", "categoria": "legal"}
    if _deburr(n).lower() in _GENERIC_LABELS:
        return {"veredicto": "descartar", "categoria": "generico"}
    return None
