"""
Validação ESTRUTURAL dos casos de golden de COMPARAÇÃO FUNDAMENTADA (explore).

Cobre a spec docs/specs/explore-grounded-comparison.md (seção "Validação"):
o gate de grounding só cobria o writing; estes casos levam a rede de segurança
para a resposta fundamentada do modo explore (recuperação de trecho para
comparação multi-edital), reusando o harness `rag` existente.

Testes PUROS: não exigem Supabase nem OpenAI. Validam só o esqueleto dos casos
(schema, ids existentes no index.json, campos obrigatórios, par de comparação).
A validação de que os chunks existem e que o recall passa exige um run com DB:
    python -m core.eval rag --no-push --limit N
Esse run foi feito (2026-06-14): gold_recall@5 0.74–1.0 nos 4 casos; "_draft"
removido. Reintroduzir "_draft" sinaliza caso não-validado.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN_PATH = ROOT / "eval_data" / "golden" / "finep.json"
INDEX_PATH = ROOT / "data" / "knowledge_graph" / "index.json"

COMPARISON_CATEGORY = "comparison_grounded"
REQUIRED_FIELDS = ("id", "edital_id", "query", "expected", "gold_text", "category")


def _load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _comparison_cases() -> list[dict]:
    g = _load_golden()
    return [q for q in g.get("queries", []) if q.get("category") == COMPARISON_CATEGORY]


def _index_edital_ids() -> set[str]:
    idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {str(e["id"]) for e in idx.get("editais", [])}


def _index_by_id() -> dict[str, dict]:
    idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {str(e["id"]): e for e in idx.get("editais", [])}


# ---------------------------------------------------------------------------
# Existência / esqueleto
# ---------------------------------------------------------------------------

def test_golden_finep_is_valid_json():
    g = _load_golden()
    assert isinstance(g, dict)
    assert isinstance(g.get("queries"), list)


def test_has_at_least_one_comparison_case():
    """A spec pede 1-2 casos de comparação; aqui há 2 grupos × 2 lados."""
    cases = _comparison_cases()
    assert len(cases) >= 2, "esperado >=2 linhas de comparação fundamentada"


def test_comparison_cases_have_required_fields():
    for c in _comparison_cases():
        for field in REQUIRED_FIELDS:
            assert field in c, f"caso {c.get('id')} sem campo obrigatório '{field}'"


def test_comparison_case_ids_are_unique():
    ids = [c["id"] for c in _comparison_cases()]
    assert len(ids) == len(set(ids)), f"ids duplicados: {ids}"


def test_comparison_query_and_gold_text_non_empty():
    for c in _comparison_cases():
        assert c["query"].strip(), f"{c['id']}: query vazia"
        assert c["gold_text"].strip(), f"{c['id']}: gold_text vazio"


# ---------------------------------------------------------------------------
# Consistência com o index.json (sem DB)
# ---------------------------------------------------------------------------

def test_comparison_edital_ids_exist_in_index():
    known = _index_edital_ids()
    for c in _comparison_cases():
        assert c["edital_id"] in known, (
            f"{c['id']}: edital_id {c['edital_id']} não existe em index.json"
        )


def test_comparison_editais_have_pdfs_in_index():
    """n_pdfs>0 é a heurística da spec p/ 'provável ter chunks'. Garante que os
    editais escolhidos são candidatos plausíveis a recuperação de trecho."""
    by_id = _index_by_id()
    for c in _comparison_cases():
        e = by_id[c["edital_id"]]
        assert (e.get("n_pdfs") or 0) > 0, (
            f"{c['id']}: edital {c['edital_id']} tem n_pdfs=0 — improvável ter chunks"
        )


# ---------------------------------------------------------------------------
# Semântica de "comparação": cada grupo cobre 2+ editais que compartilham tema
# ---------------------------------------------------------------------------

def test_comparison_groups_span_multiple_editais():
    """Comparação exige 2+ editais. Cada _comparison_group deve cobrir >=2
    editais distintos (um lado por edital, mesma query)."""
    groups: dict[str, set[str]] = defaultdict(set)
    for c in _comparison_cases():
        grp = c.get("_comparison_group")
        assert grp, f"{c['id']}: faltou _comparison_group"
        groups[grp].add(c["edital_id"])
    assert groups, "nenhum grupo de comparação encontrado"
    for grp, editais in groups.items():
        assert len(editais) >= 2, (
            f"grupo {grp} cobre só {editais} — comparação precisa de 2+ editais"
        )


def test_comparison_group_shares_a_theme():
    """O par de cada grupo deve compartilhar ao menos um tema no index.json —
    é o critério da spec p/ um bom candidato a comparação. Agnóstico a fonte:
    nenhuma ramificação por source."""
    by_id = _index_by_id()
    groups: dict[str, list[str]] = defaultdict(list)
    for c in _comparison_cases():
        groups[c["_comparison_group"]].append(c["edital_id"])
    for grp, editais in groups.items():
        theme_sets = [set(by_id[e].get("themes") or []) for e in editais]
        shared = set.intersection(*theme_sets) if theme_sets else set()
        assert shared, f"grupo {grp} ({editais}) não compartilha nenhum tema"


def test_comparison_group_same_query_across_sides():
    """Os dois lados de um grupo comparam o MESMO aspecto → mesma query."""
    groups: dict[str, set[str]] = defaultdict(set)
    for c in _comparison_cases():
        groups[c["_comparison_group"]].add(c["query"])
    for grp, queries in groups.items():
        assert len(queries) == 1, (
            f"grupo {grp} tem queries divergentes entre lados: {queries}"
        )


# ---------------------------------------------------------------------------
# Caminho de eval escolhido: gold_text (chunking-invariante), não section exata
# ---------------------------------------------------------------------------

def test_comparison_cases_use_gold_text_path_not_section():
    """expected=[] de propósito: sem DB não temos source_file/section exatos;
    o sinal vem do gold_recall_at_k (token-recall chunking-invariante)."""
    for c in _comparison_cases():
        assert c["expected"] == [], (
            f"{c['id']}: comparação deve usar gold_text (expected=[]), não section"
        )


def test_comparison_cases_validated_not_draft():
    """Validados com DB (run rag 2026-06-14, gold_recall@5 0.74–1.0): os casos
    não são mais rascunho. Re-introduzir _draft sinaliza caso não-validado."""
    for c in _comparison_cases():
        assert "_draft" not in c, (
            f"{c['id']}: _draft removido após validação com DB; não reintroduzir"
        )
