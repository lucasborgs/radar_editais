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

from config import BRONZE_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR
from core import wiki_schema
from core.pme_filter import relevance_with_reason
from domain.vocabulary import canonicalize_themes

# Schema autoritativo em WIKI.md (ver core.wiki_schema).
# Pós-Épico C: source aceito como parâmetro pelas funções públicas. Default
# preserva FINEP-only (multifonte real entra no Épico D, com loop sobre o
# SCRAPER_REGISTRY).
_DEFAULT_SOURCE = "finep"

# =============================================================================
# PATHS
# =============================================================================

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"
INDEX_HISTORICO_FILE = KNOWLEDGE_GRAPH_DIR / "index_historico.json"
FILTER_REJECTIONS_LOG = KNOWLEDGE_GRAPH_DIR / ".filter_rejections.jsonl"


# =============================================================================
# CARREGAMENTO BRONZE
# =============================================================================

def load_bronze(source: str = _DEFAULT_SOURCE) -> list[dict]:
    """Carrega o arquivo bronze mais recente da fonte.

    Usa apenas o último arquivo: scrapers de fontes com API filtrada
    server-side (FINEP/Liferay com `situacao=aberta`) tornam o scrape mais
    recente autoritativo. Acumular arquivos antigos fazia chamadas
    encerradas resurgirem no índice.
    """
    raw_dir = BRONZE_DIR / f"{source}_raw"
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
        print(f"Bronze carregado ({source}): {latest.name} ({len(chamadas)} chamadas)")
        return chamadas
    except Exception as e:
        print(f"Erro ao ler {latest.name}: {e}")
        return []


# Alias depreciado — mantido por compat até o próximo commit que renomeie callers.
load_finep_bronze = load_bronze


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


def _extract_id_finep(chamada: dict) -> str:
    cid = chamada.get("chamada_id", "")
    if cid:
        return str(cid)
    link = chamada.get("link", "")
    m = re.search(r"/chamadapublica/(\d+)", link)
    return m.group(1) if m else link.split("/")[-1]


_FAPESP_URL_ID_RE = re.compile(r"/(\d+)(?:/|$)")


def _extract_id_fapesp(chamada: dict) -> str:
    url = chamada.get("url", "")
    m = _FAPESP_URL_ID_RE.search(url)
    return m.group(1) if m else ""


# Função-dispatcher: extrai o `native_id` de acordo com a fonte. Adicionar
# fonte = registrar a função aqui (paralelo ao SCRAPER_REGISTRY).
_ID_EXTRACTORS = {
    "finep": _extract_id_finep,
    "fapesp": _extract_id_fapesp,
}


# =============================================================================
# CONSTRUÇÃO DOS EDITAIS
# =============================================================================

def _normalize_finep(ch: dict) -> dict:
    """Normaliza um item bronze FINEP → entry parcial (sem id, source, n_pdfs).
    Schema bronze: chamada_id, titulo, status, prazo_envio, data_publicacao,
    link, tema, publico_alvo, fonte_recurso."""
    deadline_str = ch.get("prazo_envio", "")
    raw_status = ch.get("status", "Desconhecido")
    themes_raw = _split_multi(ch.get("tema"))
    pub_date = ch.get("data_publicacao", "")
    fontes, subprogramas = _split_fontes(ch.get("fonte_recurso"))
    return {
        "title":         ch.get("titulo", ""),
        "status":        wiki_schema.normalize_status(raw_status, deadline_str),
        "deadline":      deadline_str,
        "pub_date":      pub_date,
        "pub_year":      wiki_schema.parse_pub_year(pub_date),
        "link":          ch.get("link", ""),
        "themes":        canonicalize_themes(themes_raw),
        "themes_raw":    themes_raw,
        "publico_alvo":  _normalize_publico(ch.get("publico_alvo")),
        "fonte_recurso": fontes,
        "subprogramas":  subprogramas,
    }


def _normalize_fapesp(ch: dict) -> dict:
    """Normaliza um item bronze FAPESP → entry parcial.
    Schema bronze: titulo, url, data_limite (ISO yyyy-mm-dd), status,
    modalidades, areas, texto_cru, fluxo_continuo. Detalhes: wikis/fapesp.md §3.
    """
    # Deadline: bronze FAPESP emite ISO; canônico é dd/mm/yyyy.
    deadline_iso = ch.get("data_limite") or ""
    deadline_str = wiki_schema.iso_to_br_date(deadline_iso) if deadline_iso else ""
    raw_status = ch.get("status") or "Desconhecido"
    themes_raw = _split_multi(ch.get("areas"))
    # FAPESP bronze não emite publico_alvo. Filtro PME apoia em modalidade.
    return {
        "title":         ch.get("titulo", ""),
        "status":        wiki_schema.normalize_status(raw_status, deadline_str),
        "deadline":      deadline_str,
        "pub_date":      "",
        "pub_year":      wiki_schema.parse_pub_year(""),
        "link":          ch.get("url", ""),
        "themes":        canonicalize_themes(themes_raw),
        "themes_raw":    themes_raw,
        "publico_alvo":  [],
        "fonte_recurso": ["FAPESP"],
        "subprogramas":  [],
        # modalidade é campo extra usado pelo filtro PME (e por nada mais)
        "modalidade":    ch.get("modalidades"),
    }


_NORMALIZERS = {
    "finep": _normalize_finep,
    "fapesp": _normalize_fapesp,
}


def _ict_requirement_regex() -> "re.Pattern | None":
    """Compila os patterns §5.10 numa regex única (lazy, cacheada). None se não
    houver patterns declarados."""
    if not hasattr(_ict_requirement_regex, "_cached"):
        pats = wiki_schema.ict_requirement_patterns()
        _ict_requirement_regex._cached = (
            re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
            if pats else None
        )
    return _ict_requirement_regex._cached


def _edital_text(ch: dict) -> str:
    """Texto do edital para a heurística §5.10: título + corpo + PDFs. Agnóstico
    à fonte — cobre FINEP (descricao, pdf_texts dict) e FAPESP (texto_cru)."""
    parts = [ch.get("titulo", ""), ch.get("descricao", ""), ch.get("texto_cru", "")]
    pdf_texts = ch.get("pdf_texts")
    if isinstance(pdf_texts, dict):
        parts.extend(str(v) for v in pdf_texts.values())
    elif isinstance(pdf_texts, list):
        parts.extend(str(v) for v in pdf_texts)
    return " ".join(p for p in parts if p)


def _detect_ict_requirement(ch: dict) -> bool:
    """True se o texto do edital exige parceria com ICT (heurística §5.10)."""
    rx = _ict_requirement_regex()
    return bool(rx.search(_edital_text(ch))) if rx else False


DISCOVERY_BRONZE_DIR = BRONZE_DIR / "discovery_raw"


def load_discovery_bronze() -> list[dict]:
    """Lê o bronze de descoberta (item 2.2) — oportunidades extraídas da web,
    cada uma com `source` (agência detectada ou 'web') e `native_id` próprios.
    Pega o arquivo mais recente por prefixo."""
    if not DISCOVERY_BRONZE_DIR.exists():
        return []
    records: list[dict] = []
    by_prefix: dict = {}
    for path in sorted(DISCOVERY_BRONZE_DIR.glob("*.json")):
        prefix = path.name.rsplit("_", 2)[0]
        by_prefix[prefix] = path
    for path in by_prefix.values():
        records.extend(json.loads(path.read_text(encoding="utf-8")))
    return records


def _normalize_discovery(ch: dict) -> dict:
    """Normaliza um registro de descoberta → entry parcial. A extração (LLM) já
    produz campos no schema comum; aqui só canonicalizamos e blindamos invariantes.

    Defensivo: themes ficam restritos ao vocab canônico §5.9 (a extração deve
    emitir canônicos; o que escapar é descartado para não quebrar a ponte/teste).
    """
    deadline_str = ch.get("prazo_envio", "") or ch.get("deadline", "")
    raw_status = ch.get("status", "Desconhecido")
    themes_raw = _split_multi(ch.get("tema") or ch.get("themes"))
    vocab = set(wiki_schema.tema_vocab())
    themes = [t for t in canonicalize_themes(themes_raw) if not vocab or t in vocab]
    pub_date = ch.get("data_publicacao", "")
    return {
        "title":         ch.get("titulo", "") or ch.get("title", ""),
        "status":        wiki_schema.normalize_status(raw_status, deadline_str),
        "deadline":      deadline_str,
        "pub_date":      pub_date,
        "pub_year":      wiki_schema.parse_pub_year(pub_date),
        "link":          ch.get("link", "") or ch.get("source_url", ""),
        "themes":        themes,
        "themes_raw":    themes_raw,
        "publico_alvo":  _normalize_publico(ch.get("publico_alvo")),
        "fonte_recurso": [],
        "subprogramas":  [],
    }


def _build_discovery_editais(records: list[dict]) -> list[dict]:
    """Constrói entradas de edital a partir do bronze de descoberta. Cada
    registro traz seu próprio `source` (agência ou 'web') e `native_id`; entram
    sempre como `verificacao=provisorio` (§5.11) — não passam pelo SCRAPER_REGISTRY.
    """
    editais: list[dict] = []
    seen: set[str] = set()
    for ch in records:
        source = (ch.get("source") or "web").strip().lower()
        native_id = ch.get("native_id") or wiki_schema.slugify(
            ch.get("link", "") or ch.get("titulo", "") or ch.get("title", "")
        )
        cid = f"{source}:{native_id}"
        if not native_id or cid in seen:
            continue
        seen.add(cid)
        editais.append({
            "id": cid,
            "source": source,
            **_normalize_discovery(ch),
            "n_pdfs": 0,
            "requires_ict_partner": _detect_ict_requirement(ch),
            "verificacao": "provisorio",
        })
    return editais


def _build_editais(chamadas: list[dict], source: str = _DEFAULT_SOURCE) -> list[dict]:
    """Converte chamadas bronze em entradas de edital normalizadas, agnóstico
    à fonte. `_NORMALIZERS[source]` lida com o schema bronze específico
    (§3 wikis/<source>.md); aqui adicionamos id prefixado, campo source e
    contagem de PDFs.
    """
    extractor = _ID_EXTRACTORS.get(source)
    normalizer = _NORMALIZERS.get(source)
    if extractor is None or normalizer is None:
        raise ValueError(f"Fonte sem extractor/normalizer registrado: {source!r}")

    editais: list[dict] = []
    for ch in chamadas:
        native_cid = extractor(ch)
        if not native_cid:
            continue

        cid = f"{source}:{native_cid}"

        # n_pdfs só faz sentido para fontes com PDFs em disco (FINEP).
        # Fontes html_body (FAPESP) sempre retornam 0.
        n_pdfs = 0
        if source == "finep" and (FINEP_PDFS_DIR / native_cid).exists():
            n_pdfs = len(list((FINEP_PDFS_DIR / native_cid).glob("*.pdf")))

        editais.append({
            "id": cid,
            "source": source,
            **normalizer(ch),
            "n_pdfs": n_pdfs,
            "requires_ict_partner": _detect_ict_requirement(ch),
            # Fontes do SCRAPER_REGISTRY são confiáveis (§5.11).
            "verificacao": "verificado",
        })

    # Dedup por id (primeira ocorrência vence). Bronze FAPESP legacy tem
    # duplicatas; FINEP é dedupado upstream mas defensivo aqui não custa.
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in editais:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        deduped.append(e)

    deduped.sort(key=lambda e: (wiki_schema.status_order(e["status"]), e.get("deadline") or ""))
    return deduped


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


# =============================================================================
# FILTRO PME (hook §1 + wikis/_pme_filter.md)
# =============================================================================

def _editais_to_filter_metadata(e: dict) -> dict:
    """Mapeia entry de edital → metadata aceito por core.pme_filter.

    FINEP bronze não emite `modalidade`/`programa` explícitos; usamos
    `subprogramas` (lista, já normalizada §5.6) como proxy do campo `programa`
    do filtro. Fontes futuras (FAPESP) preenchem `modalidade` no bronze.
    """
    return {
        "titulo": e.get("title", ""),
        "modalidade": e.get("modalidade"),
        "programa": " ".join(e.get("subprogramas", [])) or None,
        "categoria": e.get("categoria"),
        "descricao_resumo": e.get("descricao_resumo"),
        "publico_alvo": e.get("publico_alvo", []),
    }


def _apply_pme_filter(editais: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aplica o filtro determinístico PME a uma lista de entries.

    Retorna `(accepted, rejections)`:
      - accepted: entries com decision == "accept"
      - rejections: lista de dicts pra log estruturado, contendo
        (edital_id, source, title, decision, reason, deadline, logged_at)

    "unclear" é tratado como reject por enquanto (Fase 1 — fallback LLM
    é só implementado se taxa unclear > 10%, ver project_multi_fonte_fase1).
    """
    accepted: list[dict] = []
    rejections: list[dict] = []
    now = datetime.now().isoformat(timespec="seconds")
    for e in editais:
        metadata = _editais_to_filter_metadata(e)
        decision, reason = relevance_with_reason(metadata)
        if decision == "accept":
            accepted.append(e)
        else:
            rejections.append({
                "logged_at": now,
                "source":    e.get("source"),
                "edital_id": e["id"],
                "title":     e.get("title", ""),
                "decision":  decision,
                "reason":    reason,
                "deadline":  e.get("deadline", ""),
            })
    return accepted, rejections


def _truncate_rejections_log() -> None:
    """Zera o log de rejeições (chamado pelo `main` no início da run).

    Política atual: cada execução de `main` substitui o log — o que está
    no arquivo reflete *a última run*. Pra acumular histórico, callers
    fora de `main` devem usar `_log_rejections` diretamente.
    """
    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    FILTER_REJECTIONS_LOG.write_text("", encoding="utf-8")


def _log_rejections(rejections: list[dict]) -> None:
    """Append-only em `.filter_rejections.jsonl` (1 JSON por linha)."""
    if not rejections:
        return
    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    with FILTER_REJECTIONS_LOG.open("a", encoding="utf-8") as f:
        for r in rejections:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# =============================================================================
# CONSTRUÇÃO DOS ÍNDICES (orquestrador)
# =============================================================================

def _split_vigencia(editais: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa editais em (vigentes, todos). Vigência §7.1 WIKI.md."""
    vigentes = [
        e for e in editais
        if e.get("status") == "ABERTA"
        and not _deadline_expired(e.get("deadline"))
    ]
    return vigentes, editais


def build_indices(chamadas: list[dict], source: str = _DEFAULT_SOURCE) -> tuple[dict, dict]:
    """Retorna (index_vigentes, index_historico) de UMA fonte.

    Aplica o filtro PME e loga rejeições (append). Caller que quer log
    limpo deve chamar `_truncate_rejections_log()` antes.
    """
    all_editais = _build_editais(chamadas, source=source)
    accepted, rejections = _apply_pme_filter(all_editais)
    _log_rejections(rejections)
    vigentes, _ = _split_vigencia(accepted)
    return _make_index(vigentes, "vigentes"), _make_index(accepted, "historico")


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

def main(source: str | None = None) -> None:
    """Constrói índice multifonte. Se `source` for dado, processa só essa fonte.
    Senão, itera o SCRAPER_REGISTRY (todas as fontes registradas).

    Saídas: `index.json` (vigentes), `index_historico.json`, `.filter_rejections.jsonl`
    (substituído a cada run — uma execução = um snapshot do que foi rejeitado).
    """
    from pipeline.extractors import SCRAPER_REGISTRY  # noqa: PLC0415

    sources = [source] if source else list(SCRAPER_REGISTRY.keys())
    print("=" * 60)
    print(f"KNOWLEDGE GRAPH — fontes: {', '.join(sources)}")
    print("=" * 60)

    _truncate_rejections_log()

    all_accepted: list[dict] = []
    total_rejections = 0
    by_source_summary: list[str] = []

    for src in sources:
        chamadas = load_bronze(src)
        if not chamadas:
            print(f"  [{src}] sem chamadas no bronze, pulando.")
            continue
        editais_src = _build_editais(chamadas, source=src)
        accepted_src, rejections_src = _apply_pme_filter(editais_src)
        _log_rejections(rejections_src)
        all_accepted.extend(accepted_src)
        total_rejections += len(rejections_src)
        by_source_summary.append(
            f"  [{src}] {len(editais_src)} entries → {len(accepted_src)} aceitos, "
            f"{len(rejections_src)} rejeitados pelo filtro PME"
        )

    print("\n".join(by_source_summary))

    # Descoberta (item 2.2): oportunidades da web entram como `provisorio`,
    # fora do SCRAPER_REGISTRY. Passam pelo mesmo pme_filter.
    discovery_records = load_discovery_bronze()
    if discovery_records:
        disc_editais = _build_discovery_editais(discovery_records)
        disc_accepted, disc_rej = _apply_pme_filter(disc_editais)
        _log_rejections(disc_rej)
        all_accepted.extend(disc_accepted)
        total_rejections += len(disc_rej)
        print(f"  [descoberta] {len(disc_editais)} entries → "
              f"{len(disc_accepted)} aceitos, {len(disc_rej)} rejeitados (provisorio)")

    if not all_accepted:
        print("\nNenhum edital aceito após filtro. Verifique bronze e wikis/_pme_filter.md.")
        return

    vigentes, _ = _split_vigencia(all_accepted)
    index_vigentes = _make_index(vigentes, "vigentes")
    index_historico = _make_index(all_accepted, "historico")
    save_indices(index_vigentes, index_historico)

    print("\nVigentes por status:")
    for status, count in index_vigentes["summary"]["by_status"].items():
        print(f"  {status}: {count}")

    n_hist = index_historico["total_editais"] - index_vigentes["total_editais"]
    print(f"\nHistórico (encerrados/sem prazo): {n_hist} editais")
    print(f"Rejeitados pelo filtro: {total_rejections} (log: {FILTER_REJECTIONS_LOG.name})")
    print(f"Data de referência: {index_vigentes['reference_date']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Constrói índices do knowledge graph")
    parser.add_argument("--source", default=None,
                        help="slug da fonte (finep, fapesp, …). Omitido = todas")
    args = parser.parse_args()
    main(source=args.source)
