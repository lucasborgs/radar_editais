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
import unicodedata
from collections import defaultdict
from datetime import date, datetime

from domain.vocabulary import canonicalize_themes

from config import BRONZE_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR
from core import wiki_schema

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
    """Carrega o arquivo bronze FINEP mais recente.

    Usa apenas o último arquivo: a API Liferay filtra situacao=aberta
    server-side, então o scrape mais recente é a fonte autoritativa do
    que está aberto agora. Acumular arquivos antigos fazia chamadas
    encerradas resurgirem no índice.
    """
    raw_dir = BRONZE_DIR / "finep_raw"
    if not raw_dir.exists():
        print(f"Diretório não encontrado: {raw_dir}")
        return []

    files = sorted(raw_dir.glob("*.json"))
    if not files:
        print(f"Nenhum arquivo bronze em {raw_dir}")
        return []

    latest = files[-1]
    try:
        chamadas = json.loads(latest.read_text(encoding="utf-8"))
        print(f"Bronze carregado: {latest.name} ({len(chamadas)} chamadas)")
        return chamadas
    except Exception as e:
        print(f"Erro ao ler {latest.name}: {e}")
        return []


# =============================================================================
# NORMALIZAÇÃO
# =============================================================================

# Splitter global (§5.4 WIKI.md): apenas `;` e `|`. Vírgula aparece em nomes
# compostos ("Agricultura, agronegócio e saúde animal") e `/` é tratado pelos
# normalizadores (FINEP/FNDCT → regex casa ambas as siglas na mesma string).
_SPLIT_RE = re.compile(r"[;|]")


def _normalize_key(raw: str) -> str:
    """Lowercase + strip de acentos, para match case/accent-insensitive."""
    s = unicodedata.normalize("NFD", raw)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def _extract_canonicals(part: str, vocab: dict, accumulator: list[str]) -> bool:
    """Extrai todas as canônicas de `vocab` presentes em `part`. Retorna True se
    pelo menos uma match. Sem early-break: `"FINEP/FNDCT"` casa as duas siglas."""
    matched = False
    norm_part = _normalize_key(part)
    for alias, canonical in vocab.items():
        if re.search(rf"\b{re.escape(alias)}\b", norm_part):
            matched = True
            if canonical not in accumulator:
                accumulator.append(canonical)
    return matched


def _is_modalidade(part: str) -> bool:
    """True se `part` é uma modalidade financeira que deve ser descartada (§5.7)."""
    norm = _normalize_key(part)
    return any(m in norm for m in wiki_schema.modalidades_drop_list())


def _split_multi(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in _SPLIT_RE.split(value) if p.strip()]


def _split_fontes(value: str | None) -> tuple[list[str], list[str]]:
    """Separa o campo bruto de `fonte_recurso` em (fontes, subprogramas).

    Cascade sobre cada fragmento (split por §5.4):
      1. §5.4 fontes canônicas (quem paga)
      2. §5.6 subprogramas (CT-Infra, MOVER etc.)
      3. §5.7 drop-list de modalidades → descartado silenciosamente
      4. resto sem match → descartado (prosa, contexto regulatório)
    """
    if not value:
        return [], []
    fontes: list[str] = []
    subprogramas: list[str] = []
    fontes_vocab = wiki_schema.fontes_canonicas()
    subprog_vocab = wiki_schema.subprogramas_canonicos()
    for item in _SPLIT_RE.split(value):
        part = item.strip()
        if not part:
            continue
        matched_fonte = _extract_canonicals(part, fontes_vocab, fontes)
        matched_sub = _extract_canonicals(part, subprog_vocab, subprogramas)
        # resto (modalidade ou prosa não reconhecida) → drop silencioso
        _ = matched_fonte or matched_sub or _is_modalidade(part)
    return fontes, subprogramas


def _normalize_publico(value: str | None) -> list[str]:
    """Canonicaliza `publico_alvo` conforme §5.5 WIKI.md. Fragmentos sem match
    canônico (prosa longa, qualificadores específicos) são descartados."""
    if not value:
        return []
    vocab = wiki_schema.publicos_canonicos()
    result: list[str] = []
    for item in _SPLIT_RE.split(value):
        part = item.strip()
        if not part:
            continue
        _extract_canonicals(part, vocab, result)
    return result


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
        fontes, subprogramas = _split_fontes(ch.get("fonte_recurso"))
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
            "publico_alvo": _normalize_publico(ch.get("publico_alvo")),
            "fonte_recurso": fontes,
            "subprogramas": subprogramas,
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
    subprograma_idx: dict[str, list] = defaultdict(list)
    ano_idx: dict[str, list] = defaultdict(list)

    for e in editais:
        for t in e["themes"]:
            themes_idx[t].append(e["id"])
        for p in e["publico_alvo"]:
            publico_idx[p].append(e["id"])
        for f in e["fonte_recurso"]:
            fonte_idx[f].append(e["id"])
        for sp in e.get("subprogramas", []):
            subprograma_idx[sp].append(e["id"])
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
            "n_subprogramas": len(subprograma_idx),
            "n_anos":   len(ano_idx),
        },
        "editais": editais,
        "themes_index": dict(themes_idx),
        "publico_index": dict(publico_idx),
        "fonte_index": dict(fonte_idx),
        "subprograma_index": dict(subprograma_idx),
        "ano_index":    dict(ano_idx),
    }


def _deadline_expired(deadline_str: str | None) -> bool:
    """True se prazo está definido E já passou. False se sem prazo (programa contínuo)."""
    d = wiki_schema.parse_deadline(deadline_str)
    return d is not None and d < date.today()


def build_indices(chamadas: list[dict]) -> tuple[dict, dict]:
    """Retorna (index_vigentes, index_historico).

    Vigência (§7.1 WIKI.md):
      - status == ABERTA e (sem prazo OU prazo futuro) → vigente
      - status == ABERTA e prazo passado               → histórico
      - status == ENCERRADA                            → histórico
    """
    all_editais = _build_editais(chamadas)
    vigentes = [
        e for e in all_editais
        if e.get("status") == "ABERTA"
        and not _deadline_expired(e.get("deadline"))
    ]
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

    print("\nVigentes por status:")
    for status, count in index_vigentes["summary"]["by_status"].items():
        print(f"  {status}: {count}")

    n_hist = index_historico["total_editais"] - index_vigentes["total_editais"]
    print(f"\nHistórico (encerrados/sem prazo): {n_hist} editais")
    print(f"Data de referência: {index_vigentes['reference_date']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Constrói índices FINEP")
    parser.parse_args()  # mantém interface CLI consistente
    main()
