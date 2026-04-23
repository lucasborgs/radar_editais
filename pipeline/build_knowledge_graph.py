"""
Constrói os índices de conhecimento FINEP a partir dos dados bronze.

Input:  bronze_data/finep_raw/*.json
Output:
  - knowledge_graph/index.json           — editais vigentes (matching)
  - knowledge_graph/index_historico.json — todos os editais (histórico)

Vigência:
  - ABERTA            → vigente
  - ENCERRADA         → histórico
  - Desconhecido + prazo >= hoje → vigente
  - Desconhecido sem prazo ou prazo passado → histórico

Uso:
    python pipeline/build_knowledge_graph.py
"""
import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime

from config import BRONZE_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR
from core import wiki_schema
from domain.vocabulary import canonicalize_themes

# Schema autoritativo em WIKI.md (ver core.wiki_schema)
_SOURCE = "finep"

# =============================================================================
# PATHS
# =============================================================================

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"
INDEX_HISTORICO_FILE = KNOWLEDGE_GRAPH_DIR / "index_historico.json"


# =============================================================================
# CARREGAMENTO BRONZE
# =============================================================================

def load_finep_bronze() -> list[dict]:
    """Carrega JSONs bronze da FINEP com deduplicação por link."""
    raw_dir = BRONZE_DIR / "finep_raw"
    if not raw_dir.exists():
        print(f"Diretório não encontrado: {raw_dir}")
        return []

    seen: set[str] = set()
    chamadas: list[dict] = []
    for f in sorted(raw_dir.glob("*.json")):
        try:
            for item in json.loads(f.read_text(encoding="utf-8")):
                link = item.get("link", "")
                if link and link not in seen:
                    seen.add(link)
                    chamadas.append(item)
        except Exception as e:
            print(f"Erro ao ler {f.name}: {e}")
    return chamadas


# =============================================================================
# NORMALIZAÇÃO
# =============================================================================

def _normalize_fonte(raw: str) -> list[str]:
    """Divide e normaliza um valor de fonte_recurso para nomes canônicos (§5.4 WIKI.md)."""
    # divide por ; , | /
    parts = re.split(r"[;,|/]", raw)
    result: list[str] = []
    fontes_canonicas = wiki_schema.fontes_canonicas()
    for part in parts:
        # extrai siglas conhecidas (ex: "FNDCT – Subvenção Econômica" → "FNDCT")
        for key, canonical in fontes_canonicas.items():
            if re.search(rf"\b{key}\b", part, re.IGNORECASE):
                if canonical not in result:
                    result.append(canonical)
                break
        else:
            # sem correspondência conhecida → mantém limpo se não vazio
            clean = part.strip()
            if clean and clean not in result:
                result.append(clean)
    return result


def _split_multi(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in re.split(r"[;,|]", value) if p.strip()]


def _split_fontes(value: str | None) -> list[str]:
    if not value:
        return []
    seen: list[str] = []
    for item in re.split(r"[;,]", value):
        for f in _normalize_fonte(item.strip()):
            if f not in seen:
                seen.append(f)
    return seen


def _extract_id(chamada: dict) -> str:
    cid = chamada.get("chamada_id", "")
    if cid:
        return str(cid)
    link = chamada.get("link", "")
    m = re.search(r"/chamadapublica/(\d+)", link)
    return m.group(1) if m else link.split("/")[-1]


# =============================================================================
# CONSTRUÇÃO DOS EDITAIS
# =============================================================================

def _build_editais(chamadas: list[dict]) -> list[dict]:
    """Converte chamadas bronze em entradas de edital normalizadas."""
    editais: list[dict] = []

    for ch in chamadas:
        cid = _extract_id(ch)
        if not cid:
            continue

        n_pdfs = len(list((FINEP_PDFS_DIR / cid).glob("*.pdf"))) \
            if (FINEP_PDFS_DIR / cid).exists() else 0

        deadline_str = ch.get("prazo_envio", "")
        raw_status = ch.get("status", "Desconhecido")
        # Normalização conforme §7.2 WIKI.md (prazo futuro → ABERTA)
        status = wiki_schema.normalize_status(raw_status, deadline_str)

        themes_raw = _split_multi(ch.get("tema"))
        pub_date = ch.get("data_publicacao", "")
        editais.append({
            "id": cid,
            "title": ch.get("titulo", ""),
            "status": status,
            "deadline": deadline_str,
            "pub_date": pub_date,
            "pub_year": wiki_schema.parse_pub_year(pub_date),
            "link": ch.get("link", ""),
            "themes": canonicalize_themes(themes_raw),
            "themes_raw": themes_raw,
            "publico_alvo": _split_multi(ch.get("publico_alvo")),
            "fonte_recurso": _split_fontes(ch.get("fonte_recurso")),
            "n_pdfs": n_pdfs,
        })

    editais.sort(key=lambda e: (wiki_schema.status_order(e["status"]), e.get("deadline") or ""))
    return editais


# =============================================================================
# CONSTRUÇÃO DOS ÍNDICES
# =============================================================================

def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item.get(key, "?")] += 1
    return dict(counts)


def _make_index(editais: list[dict], label: str) -> dict:
    themes_idx: dict[str, list] = defaultdict(list)
    publico_idx: dict[str, list] = defaultdict(list)
    fonte_idx: dict[str, list] = defaultdict(list)
    ano_idx: dict[str, list] = defaultdict(list)

    for e in editais:
        for t in e["themes"]:
            themes_idx[t].append(e["id"])
        for p in e["publico_alvo"]:
            publico_idx[p].append(e["id"])
        for f in e["fonte_recurso"]:
            fonte_idx[f].append(e["id"])
        ano_idx[str(e["pub_year"])].append(e["id"])

    return {
        "total_editais": len(editais),
        "label": label,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reference_date": date.today().strftime("%Y-%m-%d"),
        "summary": {
            "by_status": _count_by(editais, "status"),
            "by_year":   _count_by(editais, "pub_year"),
            "n_themes": len(themes_idx),
            "n_publico_alvo": len(publico_idx),
            "n_fontes": len(fonte_idx),
            "n_anos":   len(ano_idx),
        },
        "editais": editais,
        "themes_index": dict(themes_idx),
        "publico_index": dict(publico_idx),
        "fonte_index": dict(fonte_idx),
        "ano_index":    dict(ano_idx),
    }


def build_indices(chamadas: list[dict]) -> tuple[dict, dict]:
    """Retorna (index_vigentes, index_historico)."""
    all_editais = _build_editais(chamadas)
    vigentes = [e for e in all_editais if wiki_schema.is_vigente(e.get("deadline"))]
    return _make_index(vigentes, "vigentes"), _make_index(all_editais, "historico")


# =============================================================================
# PERSISTÊNCIA
# =============================================================================

def save_indices(index_vigentes: dict, index_historico: dict) -> None:
    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    INDEX_FILE.write_text(
        json.dumps(index_vigentes, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Index vigentes:  {INDEX_FILE}  ({index_vigentes['total_editais']} editais)")

    INDEX_HISTORICO_FILE.write_text(
        json.dumps(index_historico, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Index histórico: {INDEX_HISTORICO_FILE}  ({index_historico['total_editais']} editais)")


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("=" * 60)
    print("KNOWLEDGE GRAPH — FINEP")
    print("=" * 60)

    chamadas = load_finep_bronze()
    print(f"Chamadas bronze carregadas: {len(chamadas)}")
    if not chamadas:
        print("Nenhuma chamada encontrada. Execute o scraper FINEP primeiro.")
        return

    index_vigentes, index_historico = build_indices(chamadas)
    save_indices(index_vigentes, index_historico)

    print(f"\nVigentes por status:")
    for status, count in index_vigentes["summary"]["by_status"].items():
        print(f"  {status}: {count}")

    n_hist = index_historico["total_editais"] - index_vigentes["total_editais"]
    print(f"\nHistórico (encerrados/sem prazo): {n_hist} editais")
    print(f"Data de referência: {index_vigentes['reference_date']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Constrói índices FINEP")
    parser.parse_args()  # mantém interface CLI consistente
    main()
