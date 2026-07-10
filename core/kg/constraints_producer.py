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


# ===========================================================================
# v3 (spec docs/specs/v3-unified.md) — entry point ADITIVO: lê o TEXTO das seções
# de elegibilidade do silver e devolve (constraints, requisitos_texto). Vocabulário
# §4.4 (10 tipos) lido do WIKI (`constraint_vocab`), separado do subconjunto v2
# acima. `produce_for_graph`/`extract_constraints`/`_normalize` (v2) NÃO mudam.
# ===========================================================================

_SYSTEM_V3 = """Você extrai ELEGIBILIDADE DURA de editais/programas brasileiros de \
fomento à inovação a partir do TEXTO das seções de elegibilidade, convertendo-o em \
constraints ESTRUTURADAS que um sistema avalia contra o perfil da empresa.

Emita SOMENTE constraints EXPLÍCITAS e VERIFICÁVEIS de UM destes tipos — na dúvida, \
NÃO emita (um constraint falso exclui a empresa por engano; o texto continua sendo \
exibido por outra via):

- porte: quem pode concorrer por tamanho. valor = lista dos portes PERMITIDOS entre \
[mei, me, epp, media, grande]; op="in". "Até média empresa" → ["mei","me","epp","media"]. \
"Micro e pequena" → ["mei","me","epp"]. Exclusão ("vedado a grandes") → op="not_in", valor=["grande"].
- faturamento: teto/piso de faturamento/receita anual. valor = número em R$/ano \
(ex. 4800000 para "R$ 4,8 milhões"); op="lte" (até) ou "gte" (a partir de).
- idade_empresa_meses: idade máxima/mínima do CNPJ em MESES. valor = número; op="lte" \
(no máximo) ou "gte" (no mínimo). "Criada há no máximo 12 meses" → op="lte", valor=12.
- sede_uf: exigência de sede/domicílio em UF. valor = lista de siglas (2 letras, ex. ["SC"]); \
op="in" (deve estar) ou "not_in".
- forma_juridica: natureza jurídica exigida. valor = lista entre \
[empresa, startup, ict, universidade, cooperativa, associacao]; op="in"/"not_in".
- trl: nível de maturidade tecnológica. valor = número 1-9; op="gte"/"lte"/"in".
- cnae: restrição por CNAE/atividade econômica. valor = lista de códigos/prefixos CNAE \
(strings, ex. ["62"]); op="in"/"not_in".
- parceria: exige parceria/consórcio com um tipo de ator. op="exige", valor = um de \
[agencia, fap, ict, corporate, aceleradora, investidor] (ex. "parceria obrigatória com ICT" → "ict").
- vinculo_incubacao: exige vínculo com incubadora/aceleradora credenciada. op="exige", valor=true.
- investidor_privado: exige aporte/compromisso de investidor privado. op="exige", valor=true.

NÃO emita constraint para: exigências documentais (plano de trabalho, certidões, CNPJ \
regular), critérios de mérito/pontuação, prazos, contrapartida financeira, setor/tema \
(afinidade, não elegibilidade), ou qualquer coisa fora dos 10 tipos acima.

Além das constraints, devolva `requisitos_texto`: uma lista curta (≤6) de frases \
objetivas em português com as EXIGÊNCIas de elegibilidade relevantes (inclusive as que \
você NÃO estruturou), para exibição no card. Não repita boilerplate.

Responda JSON: {"constraints": [{"tipo": "...", "op": "...", "valor": ...}], \
"requisitos_texto": ["..."]}. Listas vazias se nada se encaixa."""


_UF_ENUM = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]


def _v3_vocab() -> tuple[set[str], set[str], dict[str, set[str]]]:
    """(tipos, ops, valores_enum) do bloco `constraint_vocab` do WIKI (§13.4).
    Fallback à lista §4.4 embutida se o bloco estiver ausente. `valores_enum`
    traz os enums fechados de tipos categóricos (porte, forma_juridica, sede_uf)
    — achado do bake-off Fase 1.5: sem checar contra este enum o produtor
    vazava valores livres (ex. forma_juridica="sociedade limitada",
    porte="250", sede_uf="BR" num edital NACIONAL — não é sigla de UF)."""
    v = schema.constraint_vocab_v3()
    tipos = set(v.get("tipos") or [
        "porte", "faturamento", "idade_empresa_meses", "sede_uf", "forma_juridica",
        "trl", "cnae", "parceria", "vinculo_incubacao", "investidor_privado",
    ])
    ops = set(v.get("ops") or ["in", "not_in", "lte", "gte", "exige"])
    valores_raw = v.get("valores") or {
        "porte": ["mei", "me", "epp", "media", "grande"],
        "forma_juridica": ["empresa", "startup", "ict", "universidade", "cooperativa", "associacao"],
        "sede_uf": _UF_ENUM,
    }
    valores = {k: {str(x).strip().lower() for x in vs} for k, vs in valores_raw.items()}
    return tipos, ops, valores


# tipos categóricos com enum fechado + op esperado (in/not_in — lte/gte não faz
# sentido para um vocabulário categórico, ex. porte="250" com op="lte"). Um
# valor de sede_uf fora das 27 UFs (ex. "BR", "Brasil", "nacional") indica
# edital NACIONAL — não é constraint de sede, então NÃO deve ser emitido.
_CATEGORICAL_ENUM_TIPOS = {"porte", "forma_juridica", "sede_uf"}


def _valid_v3(c: dict, tipos: set[str], ops: set[str], valores: dict[str, set[str]]) -> bool:
    if not (
        isinstance(c, dict)
        and c.get("tipo") in tipos
        and c.get("op") in ops
        and c.get("valor") not in (None, "", [])
    ):
        return False
    tipo, op, valor = c["tipo"], c["op"], c["valor"]
    if tipo in _CATEGORICAL_ENUM_TIPOS:
        enum = valores.get(tipo)
        if op not in ("in", "not_in") or not enum:
            return False
        vals = valor if isinstance(valor, list) else [valor]
        if not vals or any(str(v).strip().lower() not in enum for v in vals):
            return False
    return True


def _normalize_v3(c: dict) -> dict:
    """Normaliza o `valor` para a forma canônica que o avaliador compara (v3):
    UF maiúsculo, porte/forma_juridica/cnae minúsculo, números como número,
    `exige` como bool/string enxuto."""
    tipo, op, valor = c["tipo"], c["op"], c["valor"]
    if tipo == "sede_uf":
        vals = valor if isinstance(valor, list) else [valor]
        c["valor"] = [str(v).strip().upper() for v in vals]
    elif tipo in ("porte", "forma_juridica"):
        vals = valor if isinstance(valor, list) else [valor]
        c["valor"] = [str(v).strip().lower() for v in vals]
    elif tipo == "cnae":
        vals = valor if isinstance(valor, list) else [valor]
        c["valor"] = [str(v).strip() for v in vals if str(v).strip()]
    elif tipo in ("faturamento", "trl", "idade_empresa_meses"):
        if isinstance(valor, list):
            c["valor"] = [_num(v) for v in valor if _num(v) is not None]
        else:
            c["valor"] = _num(valor)
    elif op == "exige":
        # parceria → tipo de ator (string minúscula); vinculo/investidor → bool.
        if tipo == "parceria":
            c["valor"] = str(valor).strip().lower()
        else:
            c["valor"] = bool(valor) if not isinstance(valor, str) else valor.strip().lower() in ("true", "sim", "1", "yes")
    return c


def produce_from_text(
    text: str, *, client=None, model: str | None = None
) -> tuple[list[dict], list[str]]:
    """Extrai `(constraints, requisitos_texto)` do TEXTO das seções de elegibilidade
    do silver (entry point v3 — não toca o grafo).

    Fail-open: qualquer erro (sem chave, parse, chamada) → `([], [])` (a entidade
    cai no "unknown não elimina"). CONSERVADOR: constraint inválida/fora do vocab é
    descartada; o texto continua em `requisitos_texto`. Não levanta."""
    text = (text or "").strip()
    if not text:
        return [], []
    try:
        if client is None:
            client, model = _make_llm()
        model = model or CONSTRAINTS_MODEL
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_V3},
                {"role": "user", "content": text[:12000]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:  # noqa: BLE001 — produtor nunca derruba o ingest
        logger.warning("produce_from_text: falha (%s) — sem constraints", e)
        return [], []

    tipos, ops, valores = _v3_vocab()
    constraints: list[dict] = []
    for c in data.get("constraints", []) or []:
        if not isinstance(c, dict):
            continue
        c = {"tipo": c.get("tipo"), "op": c.get("op"), "valor": c.get("valor")}
        if _valid_v3(c, tipos, ops, valores):
            constraints.append(_normalize_v3(c))
        else:
            logger.info("produce_from_text: constraint descartada (fora do vocab): %s", c)

    requisitos = [
        str(r).strip() for r in (data.get("requisitos_texto") or [])
        if isinstance(r, str) and str(r).strip()
    ][:8]
    return constraints, requisitos


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
