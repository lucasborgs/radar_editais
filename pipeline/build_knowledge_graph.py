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
from pathlib import Path

from config import BRONZE_DIR, FINEP_FACTS_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR

# =============================================================================
# PATHS
# =============================================================================

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"
INDEX_HISTORICO_FILE = KNOWLEDGE_GRAPH_DIR / "index_historico.json"


# =============================================================================
# VIGÊNCIA
# =============================================================================

def _parse_deadline(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _is_vigente(entry: dict) -> bool:
    """Retorna True apenas se o edital tem prazo preenchido e após hoje."""
    d = _parse_deadline(entry.get("deadline"))
    return d is not None and d > date.today()


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

def _split_multi(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in re.split(r"[;,|]", value) if p.strip()]


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

        facts_file = FINEP_FACTS_DIR / f"{cid}.json"
        n_facts = 0
        if facts_file.exists():
            try:
                n_facts = len(json.loads(facts_file.read_text(encoding="utf-8")).get("facts", []))
            except Exception:
                pass

        deadline_str = ch.get("prazo_envio", "")
        raw_status = ch.get("status", "Desconhecido")
        # Se o prazo é futuro, o edital está obrigatoriamente aberto
        deadline_date = _parse_deadline(deadline_str)
        if deadline_date and deadline_date > date.today():
            status = "ABERTA"
        elif raw_status == "ENCERRADA":
            status = "ENCERRADA"
        else:
            status = raw_status

        editais.append({
            "id": cid,
            "title": ch.get("titulo", ""),
            "status": status,
            "deadline": deadline_str,
            "pub_date": ch.get("data_publicacao", ""),
            "link": ch.get("link", ""),
            "themes": _split_multi(ch.get("tema")),
            "publico_alvo": _split_multi(ch.get("publico_alvo")),
            "fonte_recurso": _split_multi(ch.get("fonte_recurso")),
            "n_pdfs": n_pdfs,
            "n_facts": n_facts,
        })

    status_order = {"ABERTA": 0, "Desconhecido": 1, "ENCERRADA": 2}
    editais.sort(key=lambda e: (status_order.get(e["status"], 9), e.get("deadline") or ""))
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

    for e in editais:
        for t in e["themes"]:
            themes_idx[t].append(e["id"])
        for p in e["publico_alvo"]:
            publico_idx[p].append(e["id"])
        for f in e["fonte_recurso"]:
            fonte_idx[f].append(e["id"])

    return {
        "total_editais": len(editais),
        "label": label,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reference_date": date.today().strftime("%Y-%m-%d"),
        "summary": {
            "by_status": _count_by(editais, "status"),
            "n_themes": len(themes_idx),
            "n_publico_alvo": len(publico_idx),
            "n_fontes": len(fonte_idx),
        },
        "editais": editais,
        "themes_index": dict(themes_idx),
        "publico_index": dict(publico_idx),
        "fonte_index": dict(fonte_idx),
    }


def build_indices(chamadas: list[dict]) -> tuple[dict, dict]:
    """Retorna (index_vigentes, index_historico)."""
    all_editais = _build_editais(chamadas)
    vigentes = [e for e in all_editais if _is_vigente(e)]
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
