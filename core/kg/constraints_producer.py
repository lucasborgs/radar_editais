"""Produtor de constraints de elegibilidade dura (KG v2, PR5) — build-time.

Estrutura o texto residual de elegibilidade (`requisitos_texto[]` /
`exclusoes_texto[]`, foldado no PR2) em `constraints[]` tipadas
(`{tipo, op, valor}`, schema D6/WIKI §6.4) que o avaliador determinístico
(`core/services/eligibility.py`) consegue casar contra o perfil.

É um passe LLM de BUILD (não runtime), irmão da higiene de Conceitos
(`canonicalize.py`): mesmo cliente, JSON mode, temperature 0, fail-open (erro de
infra nunca derruba o build — a Oportunidade fica sem constraints, cai no
"unknown não elimina"). CONSERVADOR por desenho: um constraint FALSO vira
eliminação falsa no filtro duro do match, então na dúvida NÃO emite (o texto
continua em `requisitos_texto`, informando sem gate).
"""
from __future__ import annotations

import json
import logging
import os

from core.kg import schema

logger = logging.getLogger(__name__)

# Modelo do produtor — build-time, desacoplado do OPENAI_MODEL global (mesma
# razão do CANON_MODEL/HYPER_EXTRACT_MODEL: artefato de build estável).
CONSTRAINTS_MODEL = os.environ.get("CONSTRAINTS_MODEL", "gpt-4o-mini")

# Vocabulário de porte (ordinal) — o produtor expande "até X" para o conjunto.
_PORTE_ORDER = ["mei", "me", "epp", "media", "grande"]

_SYSTEM = """Você extrai ELEGIBILIDADE DURA de editais brasileiros de fomento à \
inovação, convertendo texto de requisitos/exclusões em constraints ESTRUTURADAS \
que um sistema avalia contra o perfil da empresa.

Emita SOMENTE constraints EXPLÍCITAS e VERIFICÁVEIS de UM destes tipos — na \
dúvida, NÃO emita (o texto continua informando por outra via; um constraint \
falso exclui a empresa por engano):

- porte: quem pode concorrer por tamanho. valor = lista dos portes PERMITIDOS \
entre [mei, me, epp, media, grande]; op="in". "Até média empresa" → \
["mei","me","epp","media"]. "Micro e pequena" → ["mei","me","epp"]. Se o texto \
EXCLUI um porte ("vedado a grandes empresas") → op="not_in", valor=["grande"].
- sede_uf: exigência de sede/domicílio em UF. valor = lista de siglas de UF \
(2 letras, ex. ["SC"]); op="in" (deve estar) ou "not_in".
- faturamento: teto/piso de faturamento anual. valor = número em R$/ano \
(ex. 16000000); op="lte" (até) ou "gte" (a partir de).
- trl: nível de maturidade tecnológica exigido. valor = número 1-9; op="gte" \
(mínimo), "lte" (máximo) ou "in" com lista de níveis.
- forma_juridica: natureza jurídica exigida. valor = lista entre \
[empresa, startup, ict, universidade, cooperativa, associacao]; op="in"/"not_in".
- parceria: exige parceria/consórcio com um tipo de ator. op="exige", \
valor = um de [agencia, fap, ict, corporate, aceleradora, investidor] \
(ex. "obrigatória parceria com ICT" → valor="ict").

NÃO emita constraint para: exigências documentais (plano de trabalho, certidões, \
CNPJ regular), critérios de mérito/pontuação, prazos, valores de contrapartida, \
setor/tema (isso é afinidade, não elegibilidade), ou qualquer coisa fora dos 6 \
tipos acima.

Responda JSON: {"constraints": [{"tipo": "...", "op": "...", "valor": ...}]}. \
Lista vazia se nada se encaixa."""


def _make_llm():
    from core.llm.llm_client import make_client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida (o produtor de constraints usa LLM)")
    return make_client(api_key=api_key, max_retries=6), CONSTRAINTS_MODEL


def _valid(c: dict) -> bool:
    """Constraint bem-formada e dentro dos enums do WIKI (§6.4)."""
    tipos, ops = set(schema.constraint_tipos()), set(schema.constraint_ops())
    return (
        isinstance(c, dict)
        and c.get("tipo") in tipos
        and c.get("op") in ops
        and c.get("valor") not in (None, "", [])
    )


def _normalize(c: dict) -> dict:
    """Normaliza o `valor` para a forma que o avaliador compara (porte/UF em
    minúsculo/maiúsculo canônico; números como número)."""
    tipo, valor = c["tipo"], c["valor"]
    if tipo == "sede_uf":
        vals = valor if isinstance(valor, list) else [valor]
        c["valor"] = [str(v).strip().upper() for v in vals]
    elif tipo in ("porte", "forma_juridica"):
        vals = valor if isinstance(valor, list) else [valor]
        c["valor"] = [str(v).strip().lower() for v in vals]
    elif tipo in ("faturamento", "trl"):
        if isinstance(valor, list):
            c["valor"] = [_num(v) for v in valor if _num(v) is not None]
        else:
            c["valor"] = _num(valor)
    return c


def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def extract_constraints(
    requisitos: list[str], exclusoes: list[str], *, client=None, model: str | None = None
) -> list[dict]:
    """LLM estrutura os textos residuais em `constraints[]` válidas e normalizadas.

    Fail-open: qualquer erro (sem chave, parse, chamada) → `[]` (a Oportunidade
    fica sem constraints; o card cai no "unknown não elimina"). Não levanta."""
    if not requisitos and not exclusoes:
        return []
    try:
        if client is None:
            client, model = _make_llm()
        model = model or CONSTRAINTS_MODEL
        payload = {"requisitos": list(requisitos or []), "exclusoes": list(exclusoes or [])}
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = json.loads(resp.choices[0].message.content).get("constraints", [])
    except Exception as e:  # noqa: BLE001 — produtor nunca derruba o build
        logger.warning("extract_constraints: falha (%s) — sem constraints", e)
        return []

    out: list[dict] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        c = {"tipo": c.get("tipo"), "op": c.get("op"), "valor": c.get("valor")}
        if _valid(c):
            out.append(_normalize(c))
        else:
            logger.info("extract_constraints: descartado (fora do schema): %s", c)
    return out


def _edital_nodes(graph: dict) -> list[dict]:
    return [
        n for n in graph.get("nodes", [])
        if n.get("type") == "Oportunidade" and n.get("kind") == "edital"
    ]


def produce_for_graph(graph: dict, *, client=None, model: str | None = None, overwrite: bool = False) -> int:
    """Preenche `constraints[]` nas Oportunidades(edital) do grafo a partir do
    texto residual. Muta os nós in-place. Retorna nº de nós com constraints
    produzidas. Idempotente com `overwrite=False` (pula nós já com constraints)."""
    n_filled = 0
    for node in _edital_nodes(graph):
        if node.get("constraints") and not overwrite:
            continue
        cons = extract_constraints(
            node.get("requisitos_texto") or [],
            node.get("exclusoes_texto") or [],
            client=client, model=model,
        )
        if cons:
            node["constraints"] = cons
            n_filled += 1
    return n_filled
