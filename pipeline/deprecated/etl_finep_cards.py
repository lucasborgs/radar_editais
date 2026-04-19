"""
Gera cards ricos por edital FINEP (Karpathy-style).

Para editais com fatos atômicos extraídos do PDF: LLM sintetiza um card
estruturado consolidando metadados + fatos. Para editais sem fatos: card
mínimo gerado diretamente dos metadados do index.json (sem LLM).

Input:
  knowledge_graph/index.json      — metadados de todos os editais
  silver_data/finep/facts/{id}.json — fatos atômicos (opcional)

Output:
  knowledge_graph/cards/{id}.json — card rico por edital

Uso:
    python pipeline/etl_finep_cards.py --backend gemini
    python pipeline/etl_finep_cards.py --edital 774 775
    python pipeline/etl_finep_cards.py --only-with-facts
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from config import KNOWLEDGE_GRAPH_DIR, KG_CARDS_DIR, FINEP_FACTS_DIR

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"
CACHE_FILE = KG_CARDS_DIR / ".cards_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# PROMPT DE SÍNTESE
# =============================================================================

SYNTHESIS_PROMPT = """Você é um especialista em fomento à inovação no Brasil.
A partir dos metadados e fatos abaixo de uma chamada pública FINEP, gere um card estruturado em JSON.

Regras:
- Responda APENAS com o JSON, sem markdown ou texto extra
- Campos null quando a informação não estiver disponível
- "mechanism": "subvencao" | "reembolsavel" | "investimento" | "misto" | null
- "eligible_entities": lista como ["empresas", "startups", "ICTs", "universidades"]
- "value_range": valores em reais como inteiros (ex: 500000), null se não informado
- "trl_range": inteiros de 1 a 9, null se não informado
- "key_requirements": máximo 5 requisitos objetivos e concretos
- "key_facts": os 5 fatos mais relevantes para uma empresa avaliar se deve se candidatar

Metadados:
{metadata}

Fatos extraídos do edital:
{facts}

Responda com este JSON:
{{
  "objective": "síntese em 2-3 frases do que este edital financia e para quem",
  "mechanism": "subvencao|reembolsavel|investimento|misto|null",
  "eligible_entities": [],
  "eligible_sectors": [],
  "value_range": {{"min_brl": null, "max_brl": null}},
  "trl_range": {{"min": null, "max": null}},
  "required_certifications": [],
  "counterpart_required": false,
  "key_requirements": [],
  "key_facts": []
}}"""


# =============================================================================
# LLM CLIENT
# =============================================================================

def _make_llm_client(backend: str):
    from openai import OpenAI

    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or \
                  getpass.getpass("Gemini API Key: ")
        return OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"
    elif backend == "openai":
        api_key = os.getenv("OPENAI_API_KEY") or getpass.getpass("OpenAI API Key: ")
        return OpenAI(api_key=api_key), "gpt-4o-mini"
    else:
        raise ValueError(f"Backend desconhecido: {backend}")


# =============================================================================
# CACHE
# =============================================================================

def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    KG_CARDS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _facts_hash(facts: list[dict]) -> str:
    text = json.dumps(facts, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(text.encode()).hexdigest()


# =============================================================================
# LEITURA DE DADOS
# =============================================================================

def _load_index() -> dict:
    if not INDEX_FILE.exists():
        raise FileNotFoundError(f"index.json não encontrado: {INDEX_FILE}")
    return json.loads(INDEX_FILE.read_text(encoding="utf-8"))


def _load_facts(edital_id: str) -> list[dict]:
    facts_file = FINEP_FACTS_DIR / f"{edital_id}.json"
    if not facts_file.exists():
        return []
    try:
        data = json.loads(facts_file.read_text(encoding="utf-8"))
        return data.get("facts", [])
    except Exception:
        return []


# =============================================================================
# GERAÇÃO DO CARD
# =============================================================================

def _build_minimal_card(entry: dict) -> dict:
    """Card mínimo para editais sem fatos (sem LLM)."""
    return {
        "id": entry["id"],
        "title": entry["title"],
        "status": entry["status"],
        "deadline": entry["deadline"],
        "pub_date": entry.get("pub_date", ""),
        "link": entry["link"],
        "themes": entry.get("themes", []),
        "publico_alvo": entry.get("publico_alvo", []),
        "fonte_recurso": entry.get("fonte_recurso", []),
        "objective": None,
        "mechanism": None,
        "eligible_entities": entry.get("publico_alvo", []),
        "eligible_sectors": entry.get("themes", []),
        "value_range": {"min_brl": None, "max_brl": None},
        "trl_range": {"min": None, "max": None},
        "required_certifications": [],
        "counterpart_required": False,
        "key_requirements": [],
        "key_facts": [],
        "n_facts": 0,
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
        "source": "metadata_only",
    }


def _synthesize_card(
    entry: dict,
    facts: list[dict],
    client,
    model: str,
) -> dict:
    """Sintetiza card rico via LLM a partir de metadados + fatos."""
    metadata_str = json.dumps({
        "title": entry["title"],
        "status": entry["status"],
        "deadline": entry["deadline"],
        "pub_date": entry.get("pub_date", ""),
        "themes": entry.get("themes", []),
        "publico_alvo": entry.get("publico_alvo", []),
        "fonte_recurso": entry.get("fonte_recurso", []),
    }, ensure_ascii=False, indent=2)

    # Usa apenas os textos dos fatos, agrupados por seção
    facts_by_section: dict[str, list[str]] = {}
    for f in facts:
        sec = f.get("section_type", "OUTRO")
        facts_by_section.setdefault(sec, []).append(f["text"])

    facts_str = json.dumps(facts_by_section, ensure_ascii=False, indent=2)

    prompt = SYNTHESIS_PROMPT.format(
        metadata=metadata_str,
        facts=facts_str[:6000],
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1000,
        )
        raw = response.choices[0].message.content.strip()

        # Remove possível markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        synthesized = json.loads(raw)
    except Exception as e:
        logger.warning("Erro LLM para edital %s: %s — usando card mínimo", entry["id"], e)
        synthesized = {}

    return {
        "id": entry["id"],
        "title": entry["title"],
        "status": entry["status"],
        "deadline": entry["deadline"],
        "pub_date": entry.get("pub_date", ""),
        "link": entry["link"],
        "themes": entry.get("themes", []),
        "publico_alvo": entry.get("publico_alvo", []),
        "fonte_recurso": entry.get("fonte_recurso", []),
        "objective": synthesized.get("objective"),
        "mechanism": synthesized.get("mechanism"),
        "eligible_entities": synthesized.get("eligible_entities", entry.get("publico_alvo", [])),
        "eligible_sectors": synthesized.get("eligible_sectors", entry.get("themes", [])),
        "value_range": synthesized.get("value_range", {"min_brl": None, "max_brl": None}),
        "trl_range": synthesized.get("trl_range", {"min": None, "max": None}),
        "required_certifications": synthesized.get("required_certifications", []),
        "counterpart_required": synthesized.get("counterpart_required", False),
        "key_requirements": synthesized.get("key_requirements", []),
        "key_facts": synthesized.get("key_facts", []),
        "n_facts": len(facts),
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
        "source": "facts+metadata",
    }


def _save_card(card: dict) -> None:
    KG_CARDS_DIR.mkdir(parents=True, exist_ok=True)
    card_file = KG_CARDS_DIR / f"{card['id']}.json"
    card_file.write_text(
        json.dumps(card, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# =============================================================================
# MAIN
# =============================================================================

def main(
    backend: str = "gemini",
    edital_ids: list[str] | None = None,
    only_with_facts: bool = False,
    delay: float = 1.5,
) -> list[dict]:
    print("=" * 60)
    print("ETL FINEP CARDS — Karpathy-style")
    print("=" * 60)

    index = _load_index()
    all_entries = index.get("editais", [])

    if edital_ids:
        entries = [e for e in all_entries if e["id"] in edital_ids]
    elif only_with_facts:
        entries = [e for e in all_entries if e.get("n_facts", 0) > 0]
    else:
        entries = all_entries

    print(f"Editais para processar: {len(entries)}")

    cache = _load_cache()

    # Conta quantos têm fatos para decidir se inicializa LLM
    with_facts = [e for e in entries if _load_facts(e["id"])]
    needs_llm = bool(with_facts)

    client, model = None, ""
    if needs_llm:
        client, model = _make_llm_client(backend)
        print(f"Backend: {backend} | Model: {model}")
    else:
        print("Nenhum edital com fatos — gerando apenas cards mínimos")

    generated, skipped, minimal = 0, 0, 0

    for i, entry in enumerate(entries, 1):
        edital_id = entry["id"]
        facts = _load_facts(edital_id)
        cache_key = edital_id

        # Verifica cache por hash dos fatos
        current_hash = _facts_hash(facts) if facts else "no_facts"
        if cache.get(cache_key) == current_hash and (KG_CARDS_DIR / f"{edital_id}.json").exists():
            logger.info("[%d/%d] %s — cache hit, pulando", i, len(entries), edital_id)
            skipped += 1
            continue

        logger.info("[%d/%d] %s — %d fatos", i, len(entries), edital_id, len(facts))

        if facts and client:
            card = _synthesize_card(entry, facts, client, model)
            time.sleep(delay)
        else:
            card = _build_minimal_card(entry)
            minimal += 1

        _save_card(card)
        cache[cache_key] = current_hash
        _save_cache(cache)
        generated += 1

    print(f"\n{'=' * 60}")
    print(f"RESUMO: {generated} gerados ({generated - minimal} LLM, {minimal} mínimos), {skipped} em cache")
    print(f"Cards salvos em: {KG_CARDS_DIR}/")
    print(f"{'=' * 60}")

    return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera cards ricos por edital FINEP")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "openai"])
    parser.add_argument("--edital", nargs="+", help="IDs específicos (ex: 774 775)")
    parser.add_argument("--only-with-facts", action="store_true",
                        help="Processa apenas editais que têm fatos extraídos")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay entre chamadas LLM (s)")
    args = parser.parse_args()

    main(
        backend=args.backend,
        edital_ids=args.edital,
        only_with_facts=args.only_with_facts,
        delay=args.delay,
    )
