"""
Gerador Unificado de Pares Sintéticos para Fine-Tuning de Embeddings

Todas as 4 fontes produzem triplas (anchor, positive_doc, hard_negative_doc)
com prompts específicos por fonte e anchors em 1ª pessoa:

  FAPESP / FINEP  → 5 triplas por chunk de edital
    Filtro: editais >= CUTOFF_YEAR com EDITAL_KEYWORDS.

  BNDES / EMBRAPII → 25/10 triplas por documento completo (programa/unidade)
    Holdout Embrapii: 13 unidades excluídas do treino; --eval-only as processa
    separadamente, escrevendo em eval_pairs.jsonl.

Saída:
  finetune_data/synthetic_training_pairs.jsonl  — pares de treino
  finetune_data/eval_pairs.jsonl               — pares holdout (--eval-only)

Uso:
  python pipeline/etl_generate_pairs.py
  python pipeline/etl_generate_pairs.py --source BNDES --source EMBRAPII
  python pipeline/etl_generate_pairs.py --source FAPESP --cutoff 2023 --max 500
  python pipeline/etl_generate_pairs.py --eval-only
  python pipeline/etl_generate_pairs.py --backend gemini --concurrency 3
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Caminhos
# ─────────────────────────────────────────────────────────────────────────────
ROOT             = Path(__file__).parent.parent
EDITAL_CORPUS    = ROOT / "finetune_data" / "tsdae_corpus.jsonl"
PROGRAMA_CORPUS  = ROOT / "finetune_data" / "supervised_corpus.jsonl"
OUTPUT_TRAIN     = ROOT / "finetune_data" / "synthetic_training_pairs.jsonl"
OUTPUT_EVAL      = ROOT / "finetune_data" / "eval_pairs.jsonl"
CACHE_PATH       = ROOT / "finetune_data" / ".generate_pairs_cache.json"
FAPESP_MANIFEST  = ROOT / "bronze_data" / "fapesp_page_manifest.json"
FINEP_MANIFEST   = ROOT / "bronze_data" / "finep_pdf_manifest.json"

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
CUTOFF_YEAR      = 2023
TRIPLETS_PER_DOC = 5        # default — FAPESP/FINEP
TRIPLETS_PER_SOURCE: dict[str, int] = {
    "FAPESP":   5,
    "FINEP":    5,
    "BNDES":    25,
    "EMBRAPII": 10,
}

FINEP_CUTOFF_YEAR = 2018   # FINEP usa corte próprio (mais histórico que o default)
MAX_EDITAL_CHARS = 3_000    # truncamento do chunk de edital enviado ao LLM
MAX_DOC_CHARS    = 8_000    # truncamento do documento programa/unidade
SAVE_EVERY       = 50

# Keywords que indicam chunks de edital úteis para matching empresa↔edital
EDITAL_KEYWORDS = [
    "elegível", "elegibilidade", "requisito", "obrigatório",
    "área temática", "fomento", "subvenção", "bolsa", "objetivo",
    "público-alvo", "financiamento", "critério", "habilitação",
    "proponente", "beneficiário", "contrapartida", "instrumento",
    "trl", "maturidade tecnológica",
]

# Filtro FAPESP por título de chamada — substitui o filtro de ano para FAPESP.
# Lógica: REJECT tem prioridade; se não houver REJECT, precisa haver APPROVE.
FAPESP_APPROVE_KW: list[str] = [
    "PIPE", "Pesquisa Inovativa em Pequenas Empresas",
    "PITE", "Parceria para Inovação Tecnológica",
    "Centro de Pesquisa em Engenharia", "CPE",
    "Centro de Pesquisa Aplicada", "CPA",
    "PAPPE", "TECNOVA", "Validação Tecnológica",
    "Startup", "Scale-up", "Desafio de Inovação",
]

FAPESP_REJECT_KW: list[str] = [
    "Auxílio à Pesquisa - Regular", "Auxílio Regular",
    "Projeto Temático", "Auxílio à Pesquisa - Temático",
    "Jovem Pesquisador", "Jovens Doutores",
    "Bolsa de Doutorado", "Bolsa de Mestrado", "Bolsa de Pós-Doutorado",
    "Iniciação Científica", "Estágio de Pesquisa",
    "Pesquisador Visitante",
    "Organização de Reunião Científica",
    "Participação em Reunião Científica",
    "Equipamentos Multiusuários", "EMU",
    "Escola São Paulo de Ciência Avançada", "SPSAS",
    "Ensino Público", "Prêmio",
]

# 13 unidades Embrapii reservadas para avaliação (não geram pares de treino)
# Estratificadas por área: saúde, TICs, materiais, agro, mobilidade, energia, etc.
EMBRAPII_HOLDOUT: frozenset[str] = frozenset({
    "unidade_embrapii_ceinfar_usp",                         # farmacêutica
    "unidade_embrapii_fmrp_usp",                            # ciências médicas
    "unidade_embrapii_dcc_ufmg",                            # ciência da computação
    "unidade_embrapii_metropole_digital_ufrn",              # digital
    "unidade_embrapii_graphene_ucs",                        # grafeno/nanomateriais
    "unidade_embrapii_polimeros",                           # polímeros
    "unidade_embrapii_agrotec_ufms",                        # bioeconomia/agronegócio
    "unidade_embrapii_embrapa_agroenergia",                 # agroenergia
    "unidade_embrapii_powertrain_usp",                      # mobilidade/propulsão
    "unidade_embrapii_e_renova_unicamp",                    # energias renováveis
    "unidade_embrapii_vbl_iot_e_industria_4_0_von_braun",   # IoT/indústria 4.0
    "unidade_embrapii_coppe",                               # engenharia geral
    "unidade_embrapii_if_ba",                               # instituto federal
})

ALL_SOURCES = {"FAPESP", "FINEP", "BNDES", "EMBRAPII"}

# ─────────────────────────────────────────────────────────────────────────────
# Modelo Pydantic — Tripla (schema único para todas as 4 fontes)
# ─────────────────────────────────────────────────────────────────────────────
class Triplet(BaseModel):
    anchor: str = Field(description="Pergunta ou declaração em 1ª pessoa do buscador do instrumento.")
    positive_doc: str = Field(description="Trecho literal ou paráfrase fiel do texto fornecido.")
    hard_negative_doc: str = Field(
        description="Instrumento real mas incorreto para a anchor — descreva restrições de modalidades existentes. NUNCA invente nomes fictícios de programas."
    )


class TripletResponse(BaseModel):
    triplets: list[Triplet] = Field(description="Lista de triplas de treinamento conforme solicitado.")


# ─────────────────────────────────────────────────────────────────────────────
# Prompts — Schema hint comum
# ─────────────────────────────────────────────────────────────────────────────
_SCHEMA_HINT = f"""\
Retorne APENAS um objeto JSON com exatamente este formato:
{{
  "triplets": [
    {{
      "anchor": "...",
      "positive_doc": "...",
      "hard_negative_doc": "..."
    }}
  ]
}}

REGRAS:
- "anchor": primeira pessoa, simulando a dor real de quem busca o instrumento.
  Varie os perfis: empreendedor, gestor de P&D, pesquisador aplicado, líder técnico.
- "positive_doc": CITAÇÃO DIRETA ou paráfrase fiel do TEXTO FORNECIDO que resolve a anchor.
- "hard_negative_doc": instrumento similar mas incorreto para a anchor — VOCÊ CRIA este
  texto descrevendo restrições ou características de modalidades de financiamento reais.
  A armadilha deve ser específica: TRL errado, fase do programa errada, tipo de instrumento
  diferente (crédito vs subvenção), porte incompatível, ou área tecnológica divergente.
  PROIBIDO inventar nomes de programas, fundos ou chamadas fictícias. Se citar um programa
  pelo nome, ele deve existir de fato. Quando não tiver certeza, descreva as restrições
  sem usar um nome próprio.
- Gere o número de triplas solicitado com anchors diversas (diferentes necessidades
  e perfis de quem busca)."""

_SYSTEM_BASE = (
    "Você é um Engenheiro de Dados especialista em gerar datasets sintéticos para treinamento "
    "Contrastive Learning (Information Retrieval). Sua saída deve ser ESTRITAMENTE um objeto "
    "JSON válido (sem markdown extra).\n\n"
)

# ─────────────────────────────────────────────────────────────────────────────
# Prompts — FINEP
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_FINEP = _SYSTEM_BASE + """\
Contexto: FINEP (Financiadora de Estudos e Projetos) apoia inovação tecnológica com foco \
em risco tecnológico, TRL e desenvolvimento de protótipos — via subvenção econômica \
(fundo perdido) ou crédito reembolsável.

Persona da 'anchor': CEO ou Diretor de P&D de startup ou empresa inovadora.
Vocabulário da 'anchor': 'risco tecnológico', 'TRL', 'desenvolvimento de protótipo', \
'subvenção econômica', 'prova de conceito', 'escalonamento', 'pesquisa aplicada'.

Hard negative: confunda o Nível de Prontidão Tecnológica (TRL) ou o tipo de despesa.
Exemplos: se a anchor busca pesquisa pré-competitiva (TRL 3–4), o negative é uma linha \
focada em pioneirismo industrial (TRL 7–9); se a anchor quer subvenção (fundo perdido), \
o negative é crédito reembolsável; se a anchor financia pessoal, o negative é para \
aquisição de equipamentos.

""" + _SCHEMA_HINT

# ─────────────────────────────────────────────────────────────────────────────
# Prompts — FAPESP
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_FAPESP = _SYSTEM_BASE + """\
Contexto: FAPESP financia pesquisa científica e inovação no estado de SP. Destaque para o \
PIPE (pesquisa inovadora para pequenas empresas) estruturado em fases: Fase 1 (viabilidade \
técnica, até 9 meses) e Fase 2 (desenvolvimento do produto, até 2 anos). Exige pesquisador \
responsável vinculado a instituição científica reconhecida.

Persona da 'anchor': pesquisador-empreendedor, cientista-chefe de Deep Tech ou fundador \
com vínculo acadêmico.
Vocabulário da 'anchor': 'prova de conceito', 'pesquisa acadêmica', 'bolsa de pesquisa', \
'viabilidade técnica', 'pesquisador responsável', 'validação científica', 'TRL inicial'.

Hard negative: confunda as fases do PIPE ou o vínculo acadêmico exigido.
Exemplos: anchor quer 9 meses para provar viabilidade da molécula (Fase 1) → negative é \
regra da Fase 2 (desenvolvimento do produto comercializável); anchor de startup sem \
pesquisador vinculado → negative exige pesquisador com bolsa ativa em instituição pública.

""" + _SCHEMA_HINT

# ─────────────────────────────────────────────────────────────────────────────
# Prompts — BNDES
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_BNDES = _SYSTEM_BASE + """\
Contexto: BNDES financia inovação e infraestrutura para empresas de médio/grande porte. \
Principais linhas: Mais Inovação (P&D radical, subvenção parcial), Funtec (pesquisa \
pré-competitiva), Fundo Clima (projetos sustentáveis), MPME Inovadora (MPMEs inovadoras). \
Combina crédito reembolsável, subvenção e participação societária.

Persona da 'anchor': Gestor Financeiro, Coordenador de PDI ou Diretor Industrial de \
média/grande empresa (energia, manufatura, agro, mobilidade, construção).
Vocabulário da 'anchor': 'ganho de escala', 'planta piloto', 'transição energética', \
'crédito reembolsável', 'contrapartida', 'projeto âncora', 'capex de P&D'.

Hard negative: confunda inovação radical com operação padrão, ou subvenção com crédito.
Exemplos: anchor quer desenvolver rota tecnológica inédita (Mais Inovação) → negative é \
crédito para aquisição de maquinário de prateleira (BNDES Finame); anchor quer subvenção \
parcial → negative é crédito 100% reembolsável com carência; anchor de MPME → negative é \
linha exclusiva para grandes empresas.

""" + _SCHEMA_HINT

# ─────────────────────────────────────────────────────────────────────────────
# Prompts — EMBRAPII
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_EMBRAPII = _SYSTEM_BASE + """\
Contexto: Embrapii conecta empresas a Unidades credenciadas (ICTs — Institutos de Ciência \
e Tecnologia) para co-desenvolvimento tecnológico. Modelo de co-investimento: empresa + \
Embrapii + ICT dividem os custos. Cada Unidade tem especialidade técnica distinta: \
materiais avançados, TICs, biotecnologia, agronegócio, energia, manufatura, etc.

Persona da 'anchor': líder técnico, CTO ou gerente de P&D buscando ICT parceira para \
co-desenvolver produto ou processo inovador.
Vocabulário da 'anchor': 'parceiro tecnológico', 'co-desenvolvimento', 'competência técnica', \
'unidade credenciada', 'embarcar IA em microcontrolador', 'desenvolver novo polímero', \
'processo biotecnológico', 'testes em planta piloto'.

Hard negative: armadilha puramente tecnológica — competência errada, mesma qualidade de \
atendimento mas área técnica divergente.
Exemplos: anchor precisa de Hardware/IoT embarcado → negative é unidade excelente mas \
focada exclusivamente em Software/Nuvem; anchor quer desenvolver bioprocesso industrial → \
negative é unidade de manufatura mecânica avançada; anchor busca materiais para alta \
temperatura → negative é unidade de polímeros para aplicação médica.

""" + _SCHEMA_HINT

# ─────────────────────────────────────────────────────────────────────────────
# Dispatch de prompts por fonte
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPTS: dict[str, str] = {
    "FINEP":    SYSTEM_FINEP,
    "FAPESP":   SYSTEM_FAPESP,
    "BNDES":    SYSTEM_BNDES,
    "EMBRAPII": SYSTEM_EMBRAPII,
}

USER_TEMPLATE = (
    "Instrumento: {source} — {titulo}\n\n"
    "TEXTO FORNECIDO:\n{doc_text}\n\n"
    "Gere as {n} triplas de treinamento. Retorne apenas o JSON:"
)

# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────
def load_cache(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def text_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Carregamento — Editais (FAPESP/FINEP)
# ─────────────────────────────────────────────────────────────────────────────
def _build_year_lookup() -> dict[tuple[str, str], int]:
    lookup: dict[tuple[str, str], int] = {}
    if FAPESP_MANIFEST.is_file():
        try:
            for cid, info in json.loads(FAPESP_MANIFEST.read_text(encoding="utf-8")).items():
                ano = info.get("ano")
                if isinstance(ano, int):
                    lookup[("FAPESP", str(cid))] = ano
        except Exception as e:
            logger.warning("FAPESP manifest: %s", e)

    if FINEP_MANIFEST.is_file():
        try:
            for chamada_id, info in json.loads(FINEP_MANIFEST.read_text(encoding="utf-8")).items():
                for pdf in info.get("pdfs", []):
                    m = re.search(r"(\d{4})$", pdf.get("data_publicacao", ""))
                    if m:
                        lookup[("FINEP", str(chamada_id))] = int(m.group(1))
                        break
        except Exception as e:
            logger.warning("FINEP manifest: %s", e)

    logger.info("Year lookup: %d entradas", len(lookup))
    return lookup


def load_edital_chunks(
    sources: set[str],
    cutoff: int,
    max_chunks: Optional[int],
    max_per_chamada: int = 5,
) -> list[dict]:
    if not EDITAL_CORPUS.exists():
        logger.warning("Corpus de editais não encontrado: %s", EDITAL_CORPUS)
        return []

    active = sources & {"FAPESP", "FINEP"}
    if not active:
        return []

    year_lookup = _build_year_lookup()
    kw_lower = [k.lower() for k in EDITAL_KEYWORDS]

    # Coleta candidatos com score de keyword (número de keywords presentes)
    from collections import defaultdict
    by_chamada: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    skipped_src = skipped_kw = skipped_yr = 0

    with EDITAL_CORPUS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue

            src = chunk.get("source", "")
            if src not in active:
                skipped_src += 1
                continue

            text_l = chunk.get("text", "").lower()
            score = sum(1 for kw in kw_lower if kw in text_l)
            if score == 0:
                skipped_kw += 1
                continue

            cid = str(chunk.get("chamada_id", ""))
            titulo_l = chunk.get("titulo", "").lower()
            if src == "FAPESP":
                rejected = any(kw.lower() in titulo_l for kw in FAPESP_REJECT_KW)
                approved = any(kw.lower() in titulo_l for kw in FAPESP_APPROVE_KW)
                if rejected or not approved:
                    skipped_yr += 1
                    continue
            else:  # FINEP
                ano = year_lookup.get((src, cid), 0)
                src_cutoff = FINEP_CUTOFF_YEAR if src == "FINEP" else cutoff
                if ano and ano < src_cutoff:
                    skipped_yr += 1
                    continue

            by_chamada[cid].append((score, chunk))

    # Por chamada: mantém os max_per_chamada chunks com maior keyword density
    chunks: list[dict] = []
    for cid, items in by_chamada.items():
        top = sorted(items, key=lambda x: -x[0])[:max_per_chamada]
        chunks.extend(c for _, c in top)

    if max_chunks:
        chunks = chunks[:max_chunks]

    logger.info(
        "Editais: %d chunks de %d chamadas "
        "(descartados: %d fonte, %d keyword, %d antes de %d; cap=%d/chamada)",
        len(chunks), len(by_chamada),
        skipped_src, skipped_kw, skipped_yr, cutoff, max_per_chamada,
    )
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Carregamento — Programas/Unidades (BNDES/Embrapii)
# ─────────────────────────────────────────────────────────────────────────────
def load_programa_docs(
    sources: set[str],
    include_holdout: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Retorna (docs_treino, docs_holdout). Holdout = unidades Embrapii reservadas."""
    if not PROGRAMA_CORPUS.exists():
        logger.warning("Corpus de programas não encontrado: %s", PROGRAMA_CORPUS)
        return [], []

    active = sources & {"BNDES", "EMBRAPII"}
    if not active:
        return [], []

    # Agrupa segmentos por programa_id
    by_programa: dict[str, dict] = {}
    with PROGRAMA_CORPUS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seg = json.loads(line)
            except json.JSONDecodeError:
                continue

            if seg.get("source", "") not in active:
                continue

            pid = seg["programa_id"]
            if pid not in by_programa:
                by_programa[pid] = {
                    "programa_id": pid,
                    "titulo": seg["titulo"],
                    "source": seg["source"],
                    "texts": [],
                }
            by_programa[pid]["texts"].append(seg["text"])

    # Constrói texto completo do documento (truncado)
    docs_train: list[dict] = []
    docs_holdout: list[dict] = []

    for pid, doc in by_programa.items():
        full_text = "\n\n".join(doc["texts"])
        doc["full_text"] = full_text[:MAX_DOC_CHARS]
        del doc["texts"]

        if doc["source"] == "EMBRAPII" and pid in EMBRAPII_HOLDOUT:
            docs_holdout.append(doc)
        else:
            docs_train.append(doc)

    logger.info(
        "Programas/Unidades: %d treino, %d holdout (fontes: %s)",
        len(docs_train), len(docs_holdout), ", ".join(sorted(active)),
    )
    return docs_train, docs_holdout


# ─────────────────────────────────────────────────────────────────────────────
# Recuperação de JSON truncado
# ─────────────────────────────────────────────────────────────────────────────
def _recover_partial_triplets(content: str) -> list[dict]:
    """Extrai objetos JSON completos de uma resposta LLM truncada.

    Varre character a character contando '{' e '}' para delimitar cada objeto
    dentro do array, sem depender de bibliotecas externas.
    """
    arr_start = content.find("[")
    if arr_start == -1:
        return []
    arr_content = content[arr_start + 1:]
    objects: list[dict] = []
    depth = 0
    start: int | None = None
    for i, ch in enumerate(arr_content):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    objects.append(json.loads(arr_content[start : i + 1]))
                except json.JSONDecodeError:
                    pass
                start = None
    return objects


# ─────────────────────────────────────────────────────────────────────────────
# Geração assíncrona — triplas (unificada para todas as 4 fontes)
# ─────────────────────────────────────────────────────────────────────────────
async def generate_triplets(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    text: str,
    source: str,
    titulo: str,
    cache_key: str,
    cache: dict,
    model: str,
    delay: float,
    debug_extra: dict,
    n_triplets: int = TRIPLETS_PER_DOC,
) -> list[dict]:
    if not text or len(text.split()) < 30:
        return []

    # Leitura do cache — ignora entradas no formato antigo (dict único)
    if cache_key in cache:
        cached = cache[cache_key]
        if cached is None:
            return []
        if isinstance(cached, list):
            return cached
        # cached é dict (formato antigo de par único): trata como miss para regenerar

    system_prompt = _SYSTEM_PROMPTS.get(source, SYSTEM_BNDES)

    async with sem:
        if delay > 0:
            await asyncio.sleep(delay)

        # Tentativas progressivas: reduz n_triplets na última para minimizar truncamento
        attempt_schedule = [
            (n_triplets, max(8192, n_triplets * 800 + 1000)),
            (n_triplets, max(8192, n_triplets * 800 + 1000)),
            (1,          4096),
        ]

        for attempt, (req_n, max_tok) in enumerate(attempt_schedule):
            if attempt > 0:
                await asyncio.sleep(delay)
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": USER_TEMPLATE.format(
                            source=source,
                            titulo=titulo,
                            doc_text=text,
                            n=req_n,
                        )},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.85,
                    max_tokens=max_tok,
                )
                content = response.choices[0].message.content
                if not content:
                    continue

                # Tenta parse completo primeiro
                try:
                    data = json.loads(content)
                    raw_list = data.get("triplets", data) if isinstance(data, dict) else data
                except json.JSONDecodeError:
                    # JSON truncado — recupera objetos completos varrendo a string
                    raw_list = _recover_partial_triplets(content)
                    if raw_list:
                        logger.warning(
                            "JSON truncado — recuperadas %d/%d triplas para %s/%s",
                            len(raw_list), req_n, source, titulo,
                        )
                    else:
                        logger.warning(
                            "JSON inválido sem triplas recuperáveis para %s/%s "
                            "(tentativa %d/3) — retentando...",
                            source, titulo, attempt + 1,
                        )
                        continue

                if not isinstance(raw_list, list) or not raw_list:
                    logger.warning(
                        "Lista vazia ou tipo inesperado para %s/%s (tentativa %d/3) — retentando...",
                        source, titulo, attempt + 1,
                    )
                    continue

                parsed = TripletResponse.model_validate({"triplets": raw_list[:req_n]})
                results = [
                    {
                        "anchor": t.anchor,
                        "positive": t.positive_doc,
                        "negative": t.hard_negative_doc,
                        "source": source,
                        "_debug": {**debug_extra, "triplet_idx": i, "model": model},
                    }
                    for i, t in enumerate(parsed.triplets)
                ]
                cache[cache_key] = results
                return results

            except Exception as e:
                logger.warning(
                    "Erro %s/%s (tentativa %d/3): %s%s",
                    source, titulo, attempt + 1, e,
                    " — retentando..." if attempt < 2 else "",
                )

        logger.error("Falha definitiva para %s/%s após 3 tentativas", source, titulo)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Runner principal
# ─────────────────────────────────────────────────────────────────────────────
def _make_client(backend: str) -> AsyncOpenAI:
    if backend == "groq":
        api_key = os.getenv("GROQ_API_KEY") or getpass.getpass("Groq API Key: ")
        return AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    elif backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or \
                  getpass.getpass("Gemini API Key (aistudio.google.com): ")
        return AsyncOpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    else:
        api_key = os.getenv("OPENAI_API_KEY") or getpass.getpass("OpenAI API Key: ")
        return AsyncOpenAI(api_key=api_key)


async def run(args: argparse.Namespace) -> None:
    _defaults = {
        "groq":   ("llama-3.3-70b-versatile", 6.0),
        "gemini": ("gemini-2.5-flash",                4.0),
        "openai": ("gpt-4o-mini",              0.0),
    }
    default_model, default_delay = _defaults.get(args.backend, ("gpt-4o-mini", 0.0))
    model = args.model or default_model
    delay = args.delay if args.delay is not None else default_delay
    logger.info("Backend: %s | Model: %s | Delay: %.1fs | Concurrency: %d",
                args.backend, model, delay, args.concurrency)

    if args.force:
        full_cache = load_cache(CACHE_PATH)
        if args.source:
            forced_sources = {s.upper() for s in args.source}
            cache = {
                k: v for k, v in full_cache.items()
                if not (isinstance(v, list) and v and v[0].get("source") in forced_sources)
            }
            logger.info("Cache: %d entradas (entradas de %s removidas)", len(cache), forced_sources)
        else:
            cache = {}  # --force sem --source = limpa tudo
    else:
        cache = load_cache(CACHE_PATH)
    logger.info("Cache: %d entradas", len(cache))

    sources = set(s.upper() for s in args.source) if args.source else ALL_SOURCES
    client = _make_client(args.backend)
    sem = asyncio.Semaphore(args.concurrency)

    output_path = OUTPUT_EVAL if args.eval_only else OUTPUT_TRAIN
    all_results: list[dict] = []

    # ── Programas/Unidades (BNDES/Embrapii) ─────────────────────────────────
    if sources & {"BNDES", "EMBRAPII"}:
        docs_train, docs_holdout = load_programa_docs(sources)
        docs_to_process = docs_holdout if args.eval_only else docs_train
        if args.limit:
            docs_to_process = docs_to_process[:args.limit]

        if not docs_to_process:
            logger.warning("Nenhum documento de programa/unidade a processar.")
        else:
            logger.info("Gerando triplas para %d programas/unidades...", len(docs_to_process))

            def _prog_key(d: dict) -> str:
                return text_key(f"{d['programa_id']}:{d['full_text'][:500]}")

            pending = [d for d in docs_to_process if not isinstance(cache.get(_prog_key(d)), list)]
            cached_results = [
                item
                for d in docs_to_process
                if isinstance(cache.get(_prog_key(d)), list)
                for item in cache[_prog_key(d)]
            ]
            all_results.extend(cached_results)
            logger.info("%d documentos em cache, %d a gerar",
                        len(docs_to_process) - len(pending), len(pending))

            for batch_start in range(0, len(pending), SAVE_EVERY):
                batch = pending[batch_start: batch_start + SAVE_EVERY]
                tasks = [
                    generate_triplets(
                        client, sem,
                        text=doc["full_text"],
                        source=doc["source"],
                        titulo=doc["titulo"],
                        cache_key=_prog_key(doc),
                        cache=cache,
                        model=model,
                        delay=delay,
                        debug_extra={"programa_id": doc["programa_id"], "titulo": doc["titulo"]},
                        n_triplets=TRIPLETS_PER_SOURCE.get(doc["source"], TRIPLETS_PER_DOC),
                    )
                    for doc in batch
                ]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)

                new_triplets = 0
                for res in batch_results:
                    if isinstance(res, list):
                        all_results.extend(res)
                        new_triplets += len(res)
                    elif isinstance(res, Exception):
                        logger.warning("Exceção: %s", res)

                save_cache(cache, CACHE_PATH)
                logger.info("Batch %d: +%d triplas | Total: %d",
                            batch_start // SAVE_EVERY + 1, new_triplets, len(all_results))

    # ── Editais (FAPESP/FINEP) ───────────────────────────────────────────────
    if not args.eval_only and sources & {"FAPESP", "FINEP"}:
        chunks = load_edital_chunks(sources, args.cutoff, args.max, args.max_per_chamada)
        if args.limit:
            chunks = chunks[:args.limit]
        if not chunks:
            logger.warning("Nenhum chunk de edital a processar.")
        else:
            def _edital_key(c: dict) -> str:
                return text_key(c.get("text", ""))

            pending_e = [c for c in chunks if not isinstance(cache.get(_edital_key(c)), list)]
            cached_e = [
                item
                for c in chunks
                if isinstance(cache.get(_edital_key(c)), list)
                for item in cache[_edital_key(c)]
            ]
            all_results.extend(cached_e)
            logger.info("Editais: %d em cache, %d a gerar", len(chunks) - len(pending_e), len(pending_e))

            for batch_start in range(0, len(pending_e), SAVE_EVERY):
                batch = pending_e[batch_start: batch_start + SAVE_EVERY]
                tasks = [
                    generate_triplets(
                        client, sem,
                        text=chunk.get("text", "")[:MAX_EDITAL_CHARS],
                        source=chunk.get("source", ""),
                        titulo=chunk.get("titulo", "Edital de Fomento à Inovação"),
                        cache_key=_edital_key(chunk),
                        cache=cache,
                        model=model,
                        delay=delay,
                        debug_extra={
                            "chunk_id": chunk.get("id", ""),
                            "chamada_id": chunk.get("chamada_id", ""),
                            "titulo": chunk.get("titulo", ""),
                        },
                        n_triplets=TRIPLETS_PER_SOURCE.get(chunk.get("source", ""), TRIPLETS_PER_DOC),
                    )
                    for chunk in batch
                ]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)

                new_pairs = 0
                for res in batch_results:
                    if isinstance(res, list):
                        all_results.extend(res)
                        new_pairs += len(res)
                    elif isinstance(res, Exception):
                        logger.warning("Exceção: %s", res)

                save_cache(cache, CACHE_PATH)
                logger.info("Batch %d: +%d triplas | Total: %d",
                            batch_start // SAVE_EVERY + 1, new_pairs, len(all_results))

    # ── Escrita do output ────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for pair in all_results:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    from collections import Counter
    by_src = Counter(p.get("source", "?") for p in all_results)
    has_neg = sum(1 for p in all_results if p.get("negative"))

    print("\n" + "=" * 60)
    print(f"{'PARES EVAL' if args.eval_only else 'PARES TREINO'} — RESUMO")
    print("=" * 60)
    print(f"  Total de triplas   : {len(all_results):,}")
    for src, n in sorted(by_src.items()):
        print(f"  {src:<12} : {n:,}")
    print(f"  Com hard_negative  : {has_neg:,}")
    print(f"  Arquivo            : {output_path}")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gera triplas sintéticas unificadas para fine-tuning de embeddings."
    )
    p.add_argument(
        "--source", action="append", metavar="SRC",
        help="Fonte a processar: FAPESP | FINEP | BNDES | EMBRAPII (repita para múltiplas; default: todas)"
    )
    p.add_argument(
        "--cutoff", type=int, default=CUTOFF_YEAR,
        help=f"Ano mínimo de publicação dos editais FAPESP/FINEP (default: {CUTOFF_YEAR})"
    )
    p.add_argument(
        "--max", type=int, default=None,
        help="Máximo de chunks de edital a processar (default: todos)"
    )
    p.add_argument(
        "--max-per-chamada", type=int, default=5, dest="max_per_chamada",
        help="Máximo de chunks por chamada/edital, selecionados por keyword density (default: 5)"
    )
    p.add_argument(
        "--concurrency", type=int, default=5,
        help="Chamadas simultâneas ao LLM (default: 5)"
    )
    p.add_argument(
        "--backend", type=str, default="gemini", choices=["openai", "groq", "gemini"],
        help="Backend LLM: gemini | groq | openai (default: gemini)"
    )
    p.add_argument(
        "--model", type=str, default=None,
        help="Modelo a usar (default: gemini-2.5-flash / llama-3.3-70b-versatile / gpt-4o-mini)"
    )
    p.add_argument(
        "--delay", type=float, default=None,
        help="Segundos de espera entre requisições (default: 4.0 Gemini, 6.0 Groq, 0 OpenAI)"
    )
    p.add_argument(
        "--eval-only", action="store_true",
        help="Processar apenas unidades holdout Embrapii → eval_pairs.jsonl"
    )
    p.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Máximo de itens por grupo de fontes para testes rápidos (default: sem limite)"
    )
    p.add_argument(
        "--force", action="store_true",
        help="Ignorar cache e regenerar todos os pares"
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args))
