"""
Build do grafo de ICTs — consolida o bronze de ICTs em knowledge_graph/icts.json.

ICTs (WIKI.md §6.1.2) são parceiras que editais exigem; não são editais. Este
build é paralelo ao build_knowledge_graph (editais) e produz um artefato próprio,
ligado ao grafo de editais pela ponte do nó `tema`.

A ponte: `edital.themes ∩ ict.themes`. EMBRAPII descreve expertise em centenas de
áreas finas (action_lines + tech_skills); os editais usam ~poucos temas macro
(emergentes do bronze FINEP/FAPESP, ver domain/vocabulary — stub). O normalizador
mapeia fino→macro via UMA chamada LLM sobre o conjunto de rótulos distintos
(dicionário reutilizável, cacheado), aplicada deterministicamente por unidade.
Áreas sem tema-macro correspondente viram `themes_proposed` — candidatas à
expansão do vocabulário de temas (revisão humana).

Uso:
    python -m pipeline.build_ict_graph              # com normalização LLM
    python -m pipeline.build_ict_graph --no-llm     # estrutura sem mapear temas (CI/teste)
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

from config import BRONZE_DIR, KNOWLEDGE_GRAPH_DIR
from core.kg import kg_store

logger = logging.getLogger(__name__)

ICT_BRONZE_DIR = BRONZE_DIR / "ict_raw"
ICTS_FILE = KNOWLEDGE_GRAPH_DIR / "icts.json"
INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"
THEME_MAP_CACHE = KNOWLEDGE_GRAPH_DIR / ".ict_theme_map_cache.json"

_SUMMARY_MAX = 280


# =============================================================================
# CARGA
# =============================================================================

def load_bronze() -> list[dict]:
    """Lê o bronze de ICTs mais recente de cada fonte em bronze_data/ict_raw/."""
    if not ICT_BRONZE_DIR.exists():
        return []
    records: list[dict] = []
    # Um arquivo por fonte (prefixo); pega o timestamp mais novo de cada prefixo.
    by_prefix: dict[str, str] = {}
    for path in sorted(glob.glob(str(ICT_BRONZE_DIR / "*.json"))):
        prefix = os.path.basename(path).rsplit("_", 2)[0]
        by_prefix[prefix] = path  # sorted → último vence (mais recente)
    for path in by_prefix.values():
        records.extend(json.loads(open(path, encoding="utf-8").read()))
    return records


def canonical_themes() -> list[str]:
    """Vocabulário canônico de temas-macro (WIKI.md §5.9) — alvo do mapeamento
    fino→macro. Garante que ict.themes use a MESMA representação de edital.themes
    (a ponte). Fallback: temas emergentes do index.json se o vocab não existir."""
    from core.kg import wiki_schema as ws
    vocab = ws.tema_vocab()
    if vocab:
        return sorted(vocab)
    if INDEX_FILE.exists():
        idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        return sorted(idx.get("themes_index", {}).keys())
    return []


# =============================================================================
# NORMALIZADOR (fino → macro) — 1 chamada LLM sobre rótulos distintos
# =============================================================================

_MAP_SYSTEM = (
    "Você mapeia áreas de expertise de instituições de C&T para uma lista FECHADA "
    "de temas canônicos de editais de fomento. Para cada área, escolha o tema "
    "canônico mais próximo SE houver correspondência clara; caso contrário, deixe "
    "vazio (string \"\"). NUNCA invente temas fora da lista. Uma área pode mapear "
    "para no máximo um tema. Responda só com JSON: {\"area\": \"tema_ou_vazio\", ...}."
)
_MAP_USER = (
    "TEMAS CANÔNICOS (lista fechada):\n{themes}\n\n"
    "ÁREAS A MAPEAR:\n{areas}\n\n"
    "Retorne o JSON mapeando cada área exatamente como escrita acima."
)


def _cache_key(areas: list[str], themes: list[str]) -> str:
    blob = json.dumps({"a": sorted(areas), "t": sorted(themes)}, ensure_ascii=False)
    return hashlib.md5(blob.encode()).hexdigest()


def _load_cache() -> dict:
    if THEME_MAP_CACHE.exists():
        try:
            return json.loads(THEME_MAP_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


_MAP_BATCH = 60  # áreas por chamada — lote pequeno evita timeout e truncamento de saída


def _map_batch(client, model, areas: list[str], canonical: list[str]) -> dict[str, str]:
    """Uma chamada LLM sobre um lote de áreas. Falha → {} (lote vira proposto)."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _MAP_SYSTEM},
                {"role": "user", "content": _MAP_USER.format(
                    themes="\n".join(f"- {t}" for t in canonical),
                    areas="\n".join(f"- {a}" for a in areas),
                )},
            ],
            temperature=0,
            max_tokens=4096,
            timeout=120,
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning("theme map: lote de %d áreas falhou (%s) — vira proposto", len(areas), e)
        return {}


def map_areas_to_themes(areas: list[str], canonical: list[str]) -> dict[str, str]:
    """Dicionário {area_raw: tema_canônico | ""}. Cacheado por hash(areas+temas).

    Fragmenta em lotes (_MAP_BATCH) — um call grande estoura timeout/output.
    Sem LLM disponível (ou sem temas canônicos) → mapa vazio (tudo vira proposto).
    """
    if not areas or not canonical:
        return {}

    cache = _load_cache()
    key = _cache_key(areas, canonical)
    if key in cache:
        logger.info("theme map: cache hit (%d áreas)", len(areas))
        return cache[key]

    client, model = _make_client()
    if client is None:
        logger.warning("theme map: sem LLM — áreas ficam sem tema (themes_proposed)")
        return {}

    canon_set = set(canonical)
    merged: dict[str, str] = {}
    n_batches = (len(areas) + _MAP_BATCH - 1) // _MAP_BATCH
    for i in range(0, len(areas), _MAP_BATCH):
        batch = areas[i:i + _MAP_BATCH]
        logger.info("theme map: lote %d/%d (%d áreas)", i // _MAP_BATCH + 1, n_batches, len(batch))
        raw_map = _map_batch(client, model, batch, canonical)
        for a in batch:
            t = raw_map.get(a, "")
            # Sanitiza: só aceita valores da lista fechada; resto vira "".
            merged[a] = t if t in canon_set else ""

    cache[key] = merged
    THEME_MAP_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return merged


def _make_client():
    """(client, model) seguindo o padrão de core.content_library. (None, None)
    se não houver credencial — o build degrada para themes vazios."""
    from core.llm_client import make_client
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    try:
        if backend == "gemini":
            return make_client(
                api_key=os.environ["GEMINI_API_KEY"],
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            ), "gemini-2.5-flash"
        return make_client(api_key=os.environ["OPENAI_API_KEY"]), os.getenv(
            "OPENAI_MODEL", "gpt-4o-mini"
        )
    except KeyError:
        return None, None


# =============================================================================
# CONSTRUÇÃO DOS NÓS
# =============================================================================

def _summary_from_about(about: str) -> str:
    if not about:
        return ""
    text = " ".join(about.split())
    if len(text) <= _SUMMARY_MAX:
        return text
    return text[:_SUMMARY_MAX].rsplit(" ", 1)[0] + "…"


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def build_icts(bronze: list[dict], area_map: dict[str, str]) -> list[dict]:
    """Bronze + dicionário área→tema → lista de nós ict, com dedup."""
    icts: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: dict[str, int] = {}  # nome normalizado → índice em icts (dedup cross-source)

    for r in bronze:
        slug = r.get("slug") or ""
        node_id = f"{r.get('source','')}:{slug}"

        themes: list[str] = []
        proposed: list[str] = []
        for area in r.get("areas_raw", []):
            mapped = area_map.get(area, "")
            if mapped:
                if mapped not in themes:
                    themes.append(mapped)
            elif area not in proposed:
                proposed.append(area)

        node = {
            "id": node_id,
            "name": r.get("name", ""),
            "kind": r.get("kind", ""),
            "source": r.get("source", ""),
            "url": r.get("url", ""),
            "about": r.get("about", ""),
            "address": r.get("address", ""),
            "contact": r.get("contact", {}),
            "areas_raw": r.get("areas_raw", []),
            "themes": themes,
            "themes_proposed": proposed,
            "summary": _summary_from_about(r.get("about", "")),
        }

        # Dedup: por id idêntico, e por nome normalizado (mesma instituição em
        # fontes diferentes → funde areas/themes na primeira ocorrência).
        if node_id in seen_ids:
            continue
        nkey = _norm_name(node["name"])
        if nkey and nkey in seen_names:
            existing = icts[seen_names[nkey]]
            for t in themes:
                if t not in existing["themes"]:
                    existing["themes"].append(t)
            for a in node["areas_raw"]:
                if a not in existing["areas_raw"]:
                    existing["areas_raw"].append(a)
            continue

        seen_ids.add(node_id)
        if nkey:
            seen_names[nkey] = len(icts)
        icts.append(node)

    return icts


def make_ict_index(icts: list[dict]) -> dict:
    """Espelha index.json: nós + índices invertidos por tema e por proposto."""
    from collections import defaultdict
    themes_idx: dict[str, list] = defaultdict(list)
    proposed_idx: dict[str, list] = defaultdict(list)
    for n in icts:
        for t in n["themes"]:
            themes_idx[t].append(n["id"])
        for p in n["themes_proposed"]:
            proposed_idx[p].append(n["id"])
    return {
        "icts": icts,
        "total_icts": len(icts),
        "themes_index": dict(themes_idx),
        "themes_proposed_index": dict(proposed_idx),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def save(index: dict) -> None:
    # Grava icts.json local e publica no Postgres (kg_artifacts) se configurado.
    kg_store.save("icts", index)


# =============================================================================
# MAIN
# =============================================================================

def main(use_llm: bool = True) -> None:
    bronze = load_bronze()
    if not bronze:
        print("Sem bronze de ICTs (bronze_data/ict_raw/). Rode o extractor primeiro.")
        return

    canonical = canonical_themes()
    print(f"ICTs no bronze: {len(bronze)} | temas-macro canônicos: {len(canonical)}")

    distinct_areas = sorted({a for r in bronze for a in r.get("areas_raw", [])})
    area_map = map_areas_to_themes(distinct_areas, canonical) if use_llm else {}
    n_mapped = sum(1 for v in area_map.values() if v)
    print(f"Áreas distintas: {len(distinct_areas)} | mapeadas p/ tema: {n_mapped}")

    icts = build_icts(bronze, area_map)
    index = make_ict_index(icts)
    save(index)

    n_bridged = sum(1 for n in icts if n["themes"])
    print(f"\nicts.json: {len(icts)} ICTs ({n_bridged} com ponte para tema)")
    print("Por tema:")
    for theme, ids in sorted(index["themes_index"].items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(ids):>3}  {theme}")
    n_prop = len(index["themes_proposed_index"])
    print(f"\nÁreas sem tema correspondente (candidatas à expansão #1): {n_prop}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Constrói knowledge_graph/icts.json")
    parser.add_argument("--no-llm", action="store_true",
                        help="não mapeia temas (estrutura só) — útil em CI/teste")
    args = parser.parse_args()
    main(use_llm=not args.no_llm)
