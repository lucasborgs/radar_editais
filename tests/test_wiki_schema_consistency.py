"""
Valida que o schema declarado em WIKI.md + wikis/<source>.md bate com
os artefatos produzidos pelo pipeline (wiki pages, índice).

Uso:
    python tests/test_wiki_schema_consistency.py          # direto
    python -m pytest tests/test_wiki_schema_consistency.py  # com pytest

Se este teste quebra, significa que o doc e o código divergiram.
Decida: o doc está certo (ajuste o código) ou o código está certo (ajuste o doc).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import wiki_schema as ws

WIKI_DIR   = ROOT / "knowledge_graph" / "wiki"
INDEX_FILE = ROOT / "knowledge_graph" / "index.json"
ICTS_FILE  = ROOT / "knowledge_graph" / "icts.json"


def _load_wiki_pages() -> list[dict]:
    # rglob — pós-Épico B (Fase 1 multi-fonte) wiki pages vivem em subfolder
    # por fonte (KG_WIKI_DIR/{source}/{native}.json).
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in WIKI_DIR.rglob("*.json")
        if not f.name.startswith(".")
    ]


def _is_current(page: dict) -> bool:
    """Wiki page gerada pelo schema atual (não legado)."""
    meta = ws.wiki_page_fields("finep")["meta"]
    return page.get("source") in set(meta["source"]["values"])


# -----------------------------------------------------------------------------
# SCHEMA DA WIKI PAGE
# -----------------------------------------------------------------------------

def test_wiki_pages_have_all_declared_fields():
    """Toda wiki page atual deve ter todos os campos declarados em WIKI.md §4."""
    fields = ws.wiki_page_fields("finep")
    expected = set(fields["inherited"]) | set(fields["synthesized"].keys()) | set(fields["meta"].keys())

    pages = [p for p in _load_wiki_pages() if _is_current(p)]
    assert pages, "Nenhuma wiki page atual encontrada — rode o pipeline primeiro"

    for page in pages:
        missing = expected - set(page.keys())
        assert not missing, f"Wiki page {page.get('id')} faltam campos: {missing}"


def test_wiki_page_source_enum():
    """Campo `source` só aceita valores declarados em WIKI.md §4.3 (current + legacy)."""
    meta = ws.wiki_page_fields("finep")["meta"]
    allowed = set(meta["source"]["values"]) | set(meta["source"].get("legacy_values", []))
    for page in _load_wiki_pages():
        assert page["source"] in allowed, \
            f"Wiki page {page['id']}: source={page['source']!r} não está em {allowed}"


def test_wiki_page_mechanism_in_vocab():
    """Campo `mechanism` (quando não-null) bate com vocab §5.1."""
    allowed = set(ws.mechanism_vocab().keys())
    for page in _load_wiki_pages():
        m = page.get("mechanism")
        if m is None:
            continue
        assert m in allowed, f"Wiki page {page['id']}: mechanism={m!r} não está em {allowed}"


# -----------------------------------------------------------------------------
# ÍNDICE
# -----------------------------------------------------------------------------

def test_index_entries_have_inherited_fields():
    """Cada entry do index.json tem os campos herdados declarados em WIKI.md §4.1."""
    if not INDEX_FILE.exists():
        return  # sem índice = sem teste (não é erro)
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    expected = set(ws.wiki_page_fields("finep")["inherited"])
    for entry in index.get("editais", []):
        missing = expected - set(entry.keys())
        assert not missing, f"Index entry {entry.get('id')} faltam campos: {missing}"


def test_index_statuses_in_vocab():
    """Todo status no índice tem entrada no vocab §5.3."""
    if not INDEX_FILE.exists():
        return
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    declared = set(ws.load().get("status", {}).keys())
    seen = {e.get("status", "") for e in index.get("editais", [])}
    missing = seen - declared
    assert not missing, f"Status não declarados em WIKI.md §5.3: {missing}"


def test_index_has_ano_dimension():
    """Cada entry do índice tem pub_year (int ou unknown_label), e ano_index está presente."""
    if not INDEX_FILE.exists():
        return
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    unknown = ws.node_types().get("ano", {}).get("unknown_label", "desconhecido")
    for entry in index.get("editais", []):
        pub_year = entry.get("pub_year")
        assert pub_year is not None, f"Entry {entry.get('id')} sem pub_year"
        assert isinstance(pub_year, int) or pub_year == unknown, \
            f"Entry {entry.get('id')}: pub_year={pub_year!r} inválido (esperado int ou {unknown!r})"
    assert "ano_index" in index, "Índice sem ano_index (§6.1)"


def test_index_entries_have_ict_flag():
    """Toda entry do índice tem requires_ict_partner (bool) — §5.10."""
    if not INDEX_FILE.exists():
        return
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    for entry in index.get("editais", []):
        v = entry.get("requires_ict_partner")
        assert isinstance(v, bool), \
            f"Entry {entry.get('id')}: requires_ict_partner={v!r} (esperado bool)"


def test_index_entries_have_verificacao():
    """Toda entry tem verificacao em {verificado, provisorio} — §5.11."""
    if not INDEX_FILE.exists():
        return
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    allowed = set(ws.verificacao_values())
    for entry in index.get("editais", []):
        v = entry.get("verificacao")
        assert v in allowed, \
            f"Entry {entry.get('id')}: verificacao={v!r} fora de {allowed}"


# -----------------------------------------------------------------------------
# NÓ ICT (WIKI.md §6.1.2 / ict_schema)
# -----------------------------------------------------------------------------

def _load_icts() -> dict:
    return json.loads(ICTS_FILE.read_text(encoding="utf-8"))


def test_ict_nodes_have_required_fields():
    """Todo nó ict tem os campos declarados em ict_schema (node_fields/required)."""
    if not ICTS_FILE.exists():
        return  # sem artefato = sem teste (não é erro)
    sch = ws.ict_schema()
    node_fields = set(sch["node_fields"])
    required = set(sch["required_fields"])
    for n in _load_icts().get("icts", []):
        assert required <= set(n.keys()), \
            f"ICT {n.get('id')} faltam campos obrigatórios: {required - set(n.keys())}"
        unexpected = set(n.keys()) - node_fields
        assert not unexpected, f"ICT {n.get('id')} tem campos fora do schema: {unexpected}"


def test_ict_kind_and_source_in_vocab():
    """kind e source de cada ICT pertencem aos enums declarados em ict_schema."""
    if not ICTS_FILE.exists():
        return
    sch = ws.ict_schema()
    kinds, sources = set(sch["kinds"]), set(sch["sources"])
    for n in _load_icts().get("icts", []):
        assert n["kind"] in kinds, f"ICT {n['id']}: kind={n['kind']!r} fora de {kinds}"
        assert n["source"] in sources, f"ICT {n['id']}: source={n['source']!r} fora de {sources}"


def test_ict_id_format():
    """id segue '<source>:<slug>' (§6.1.2 id_format)."""
    if not ICTS_FILE.exists():
        return
    for n in _load_icts().get("icts", []):
        assert ":" in n["id"], f"ICT id sem prefixo de fonte: {n['id']!r}"
        assert n["id"].startswith(n["source"] + ":"), \
            f"ICT {n['id']}: prefixo não bate com source={n['source']!r}"


def test_ict_themes_in_canonical_vocab():
    """INVARIANTE DA PONTE: todo tema de ICT está no vocab canônico §5.9 (a mesma
    autoridade que editais usam). Senão edital.themes ∩ ict.themes nunca casa.
    Temas sem correspondência ficam em themes_proposed, não em themes."""
    if not ICTS_FILE.exists():
        return
    vocab = set(ws.tema_vocab())
    if not vocab:
        return  # vocab não declarado, nada a verificar
    for n in _load_icts().get("icts", []):
        drift = set(n["themes"]) - vocab
        assert not drift, \
            f"ICT {n['id']}: themes fora do vocab canônico §5.9 (quebra a ponte): {drift}"
        # themes e themes_proposed são disjuntos por construção (proposto = não-mapeado)
        overlap = set(n["themes"]) & set(n.get("themes_proposed", []))
        assert not overlap, f"ICT {n['id']}: tema em themes e themes_proposed: {overlap}"


def test_edital_themes_subset_of_canonical_vocab():
    """Todo tema usado por editais deve estar no vocab canônico §5.9. Tema novo no
    corpus FINEP/FAPESP sem entrada no vocab quebra aqui → adicione ao §5.9."""
    if not INDEX_FILE.exists():
        return
    vocab = set(ws.tema_vocab())
    if not vocab:
        return
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    edital_themes = set(index.get("themes_index", {}).keys())
    drift = edital_themes - vocab
    assert not drift, f"Temas de edital fora do vocab canônico §5.9: {drift}"


# -----------------------------------------------------------------------------
# PROMPT
# -----------------------------------------------------------------------------

def test_extraction_prompt_has_placeholders():
    prompt = ws.extraction_prompt("finep")
    assert "{metadata}" in prompt
    assert "{documents}" in prompt


def test_extraction_prompt_mentions_synthesized_fields():
    """O prompt deve pedir todos os campos synthesized declarados."""
    prompt = ws.extraction_prompt("finep")
    for field in ws.wiki_page_fields("finep")["synthesized"].keys():
        assert field in prompt, f"Prompt não menciona campo synthesized: {field}"


# -----------------------------------------------------------------------------
# DOCUMENTO ESTRUTURADO (silver — WIKI.md §11)
# -----------------------------------------------------------------------------

def test_structured_doc_schema_well_formed():
    """§11.1: block_fields e kinds declarados e coerentes com o invariante burro."""
    sd = ws.structured_doc_schema()
    assert sd, "structured_doc_schema ausente em WIKI.md §11.1"
    fields = set(sd.get("block_fields", {}).keys())
    assert {"idx", "doc", "page", "section_path", "kind", "text"} <= fields, \
        f"block_fields incompleto: {fields}"
    kinds = set(sd.get("kinds", []))
    assert {"heading", "paragraph", "table", "boilerplate"} <= kinds, \
        f"kinds essenciais faltando: {kinds}"


def test_structurer_prompt_has_placeholders():
    """§11.3: prompt por página tem os 4 placeholders que o structurer injeta."""
    prompt = ws.structurer_prompt()
    for ph in ("{doc_name}", "{page_num}", "{page_text}", "{carry_section_path}"):
        assert ph in prompt, f"structurer_prompt sem placeholder {ph}"


def test_structurer_params_versioned():
    """§11.2: silver_version e prompt_version existem (chaves de cache §11.4)."""
    p = ws.structurer_params()
    assert p.get("silver_version"), "structurer_params.silver_version ausente"
    assert p.get("prompt_version"), "structurer_params.prompt_version ausente"


# -----------------------------------------------------------------------------
# FILTRO PME (wikis/_pme_filter.md)
# -----------------------------------------------------------------------------

def test_pme_filter_rules_well_formed():
    """wikis/_pme_filter.md expõe programas/publicos/exclusores não-vazios."""
    rules = ws.pme_filter_rules()
    assert rules, "target_relevance_rules ausente em wikis/_pme_filter.md"
    assert rules.get("programas_pme_canonicos"), "programas_pme_canonicos vazio"
    assert rules.get("publicos_pme_canonicos"), "publicos_pme_canonicos vazio"
    assert rules.get("exclusores_academicos"), "exclusores_academicos vazio"


def test_pme_filter_publicos_subset_of_canonical():
    """publicos_pme_canonicos deve ser subset dos publicos_canonicos §5.5 —
    senão o filtro testa contra rótulo que o normalizador nunca produz."""
    rules = ws.pme_filter_rules()
    pme_pubs = set(rules.get("publicos_pme_canonicos", []))
    canonical_vals = set(ws.publicos_canonicos().values())
    drift = pme_pubs - canonical_vals
    assert not drift, f"Públicos PME fora do vocab §5.5: {drift}"


def test_pme_filter_subprograma_aliases_referenced():
    """Subprogramas-tag (centelha, mover) também devem ser sinal de
    programa-whitelist — caso contrário chamadas Centelha/MOVER puras não
    seriam aceitas pelo filtro."""
    rules = ws.pme_filter_rules()
    program_keys = set(rules.get("programas_pme_canonicos", {}).keys())
    sub_keys = set(ws.subprogramas_canonicos().keys())
    # centelha e mover precisam estar nos dois vocabulários
    for alias in ("centelha", "mover"):
        assert alias in sub_keys, f"{alias!r} ausente em subprogramas_canonicos §5.6"
        assert alias in program_keys, f"{alias!r} ausente em programas_pme_canonicos"


# -----------------------------------------------------------------------------
# RUNNER
# -----------------------------------------------------------------------------

def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
