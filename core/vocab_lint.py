"""core/vocab_lint.py — o "lint" do vocabulário canônico de temas (§5.9).

Inspirado no `lint` do llm-wiki do Karpathy e na filosofia do Radar
("AI drafts, humans decide"): a LLM PROPÕE evolução do `tema_vocab`, o humano
DECIDE. O lint NUNCA aplica mudanças — ele só:

  1. coleta EVIDÊNCIA determinística (variações fora do vocab no índice +
     `tema_livre` da web + entries sem-tema),
  2. pede UMA proposta à LLM (tier barato "fast"),
  3. grava um RELATÓRIO markdown pronto-pra-decisão, com diff colável no doc.

O doc (WIKI.md §5.9) é o DONO do vocabulário: a aplicação é manual (editar o
doc; opcionalmente _SYNONYMS em domain/vocabulary.py) e fica gated pelo
validador `tests/test_wiki_schema_consistency.py` + evals de não-regressão.

Barra de evidência: tema novo só com >=3 evidências independentes; sinônimo só
com equivalência semântica clara a um tema EXISTENTE; na dúvida, não propor.

CLI:
    python -m core.vocab_lint              # coleta + proposta LLM + relatório
    python -m core.vocab_lint --dry-run    # só coleta + imprime evidência (sem LLM)
    python -m core.vocab_lint --out <dir>  # diretório de saída (default data/vocab_lint/)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timezone

from config import ROOT
from core.kg import kg_store
from core.kg import wiki_schema as ws
from domain.vocabulary import canonicalize_themes

logger = logging.getLogger(__name__)

# Diretório de saída dos relatórios (derivado; criado on-demand, gitignored).
DEFAULT_OUT_DIR = ROOT / "data" / "vocab_lint"
# Máximo de entries sem-tema mandadas pro contexto da LLM (evita inflar o prompt).
_MAX_SEM_TEMA = 30
# Truncamento do texto descritivo de cada sem-tema.
_DESC_CAP = 300


# =============================================================================
# B1. Coleta de evidência (determinística, sem LLM)
# =============================================================================

def _agg_add(agg: dict, variacao: str, *, edital_id: str, fonte: str) -> None:
    """Acumula uma ocorrência de `variacao` em `agg` (count + exemplos + fontes)."""
    slot = agg.setdefault(variacao, {"count": 0, "exemplos": [], "fontes": set()})
    slot["count"] += 1
    if edital_id and len(slot["exemplos"]) < 10 and edital_id not in slot["exemplos"]:
        slot["exemplos"].append(edital_id)
    if fonte:
        slot["fontes"].add(fonte)


def _iter_index_entries() -> list[dict]:
    """Entries do índice vigente + histórico (dedup por id), via kg_store."""
    seen: dict[str, dict] = {}
    for historico in (False, True):
        try:
            idx = kg_store.load_index(historico=historico)
        except Exception as e:  # pragma: no cover - defensivo
            logger.warning("vocab_lint: falha ao ler índice (historico=%s): %s", historico, e)
            continue
        for e in idx.get("editais", []):
            eid = e.get("id") or ""
            if eid and eid not in seen:
                seen[eid] = e
            elif not eid:
                seen[f"__noid_{len(seen)}"] = e
    return list(seen.values())


def collect_variacoes(vocab: set[str]) -> dict:
    """Variações fora do vocab: percorre index + histórico; cada item de
    `themes_raw` cuja canonicalização NÃO está no vocab vira evidência."""
    agg: dict = {}
    for e in _iter_index_entries():
        eid = e.get("id") or ""
        source = e.get("source") or ""
        for raw in e.get("themes_raw") or []:
            canon = canonicalize_themes([raw])
            if not canon:
                continue
            c = canon[0]
            if c not in vocab:
                _agg_add(agg, c, edital_id=eid, fonte=source)
    return agg


def _fetch_discovered_rows() -> list[dict]:
    """Rows NÃO-rejeitadas da staging `discovered_opportunities` (Parte C).
    Isolada para teste (monkeypatch). [] sem acesso ao Supabase (degrada)."""
    try:
        from core.db import get_supabase_service  # noqa: PLC0415
        db = get_supabase_service()
        res = (db.table("discovered_opportunities")
                 .select("url_hash, url, raw, status")
                 .neq("status", "rejected")
                 .execute())
        return res.data or []
    except Exception as e:
        logger.warning("vocab_lint: sem acesso à staging discovered_opportunities (%s)", e)
        return []


def collect_tema_livre() -> dict:
    """`tema_livre` da web: lê a staging `discovered_opportunities` (Parte C) e
    agrega cada tema-candidato (canonicalizado p/ casar variações de grafia).
    Exclui rejeitados (não são oportunidade real → ruído de vocab)."""
    agg: dict = {}
    for row in _fetch_discovered_rows():
        raw = row.get("raw") or {}
        tl = raw.get("tema_livre")
        if not tl:
            continue
        # Mesma serialização "; ".join da Descoberta.
        parts = [p.strip() for p in str(tl).split(";") if p.strip()]
        uh = row.get("url_hash")
        eid = f"web:{uh}" if uh else (row.get("url") or "")
        for raw_t in parts:
            canon = canonicalize_themes([raw_t])
            if canon:
                _agg_add(agg, canon[0], edital_id=eid, fonte="web")
    return agg


def collect_sem_tema() -> list[dict]:
    """Entries do índice com `themes == []` → candidatos p/ a LLM inferir tema.
    Máx `_MAX_SEM_TEMA`, descrição truncada."""
    out: list[dict] = []
    for e in _iter_index_entries():
        if e.get("themes"):
            continue
        desc = (e.get("objective") or e.get("descricao") or "").strip()
        out.append({
            "id": e.get("id") or "",
            "title": (e.get("title") or "").strip(),
            "descricao": desc[:_DESC_CAP],
        })
        if len(out) >= _MAX_SEM_TEMA:
            break
    return out


def collect_evidence() -> dict:
    """Roda toda a coleta B1. Retorno serializável (fontes viram listas)."""
    vocab = set(ws.tema_vocab())
    variacoes = collect_variacoes(vocab)
    tema_livre = collect_tema_livre()
    sem_tema = collect_sem_tema()

    def _ser(agg: dict) -> dict:
        return {
            k: {"count": v["count"], "exemplos": v["exemplos"],
                "fontes": sorted(v["fontes"])}
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["count"])
        }

    return {
        "vocab": sorted(vocab),
        "variacoes": _ser(variacoes),
        "tema_livre": _ser(tema_livre),
        "sem_tema": sem_tema,
    }


def has_evidence(evidence: dict) -> bool:
    return bool(evidence["variacoes"] or evidence["tema_livre"] or evidence["sem_tema"])


# =============================================================================
# B2. Proposta (uma chamada LLM, tier "fast")
# =============================================================================

_PROPOSAL_SYSTEM = (
    "Você é um curador conservador do vocabulário canônico de temas-macro de um "
    "radar de fomento deep-tech. Receberá o vocabulário ATUAL e evidências de "
    "demanda (variações fora do vocab, temas livres da web, oportunidades sem "
    "tema). PROPONHA evolução do vocabulário, mas NUNCA aplique nada — humano "
    "decide. Regras (BARRA ALTA):\n"
    "- novos_temas: só proponha um tema novo com >=3 evidências INDEPENDENTES "
    "(ids distintos). O tema deve ser uma macro-área estratégica (não um "
    "mecanismo/programa, não um nicho).\n"
    "- sinonimos: só proponha quando a variação for SEMANTICAMENTE EQUIVALENTE a "
    "um tema que JÁ EXISTE no vocab atual (campo `canonico` deve ser um item da "
    "lista atual, exatamente).\n"
    "- Na dúvida, NÃO proponha. Se nada cruzar a barra, devolva sem_acao=true e "
    "listas vazias.\n"
    "Responda SÓ JSON, sem texto fora dele, neste formato exato:\n"
    '{"novos_temas": [{"tema": "...", "evidencia": ["ids"], "n": 5, '
    '"justificativa": "..."}], '
    '"sinonimos": [{"variacao": "...", "canonico": "<tema existente>", '
    '"evidencia": ["ids"], "justificativa": "..."}], '
    '"sem_acao": false, "notas": "..."}'
)


def _strip_code_fence(raw: str) -> str:
    """Remove cercas ```json ... ``` (padrão dos *_eval.py do repo)."""
    s = (raw or "").strip()
    if "```" in s:
        s = re.sub(r"```(?:json)?", "", s).strip()
    return s


def _empty_proposal(notas: str = "") -> dict:
    return {"novos_temas": [], "sinonimos": [], "sem_acao": True, "notas": notas}


def _build_proposal_user(evidence: dict) -> str:
    return (
        "VOCABULÁRIO ATUAL (tema_vocab):\n"
        + json.dumps(evidence["vocab"], ensure_ascii=False, indent=2)
        + "\n\nVARIAÇÕES FORA DO VOCAB (canonicalizadas → count/exemplos/fontes):\n"
        + json.dumps(evidence["variacoes"], ensure_ascii=False, indent=2)
        + "\n\nTEMA_LIVRE DA WEB (candidatos → count/exemplos):\n"
        + json.dumps(evidence["tema_livre"], ensure_ascii=False, indent=2)
        + "\n\nOPORTUNIDADES SEM TEMA (para inferir candidatos):\n"
        + json.dumps(evidence["sem_tema"], ensure_ascii=False, indent=2)
    )


def _make_client():
    """(client, model) tier 'fast' por LLM_BACKEND. (None, None) se sem credencial
    — caller degrada."""
    from core.llm.llm_client import make_chat_client
    from core.llm_router import resolve_model
    try:
        return make_chat_client(openai_model=resolve_model("fast"))
    except (KeyError, ValueError):
        return None, None


def propose(evidence: dict, *, client=None, model: str | None = None) -> dict:
    """Pede UMA proposta à LLM a partir das evidências. Parsing defensivo:
    qualquer falha → proposta vazia (sem_acao). Injeção de client/model p/ teste."""
    if client is None:
        client, model = _make_client()
    if client is None:
        return _empty_proposal("sem credencial LLM — proposta não gerada")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _PROPOSAL_SYSTEM},
                      {"role": "user", "content": _build_proposal_user(evidence)}],
            temperature=0,
            max_tokens=1500,
        )
        raw = resp.choices[0].message.content
        data = json.loads(_strip_code_fence(raw))
    except Exception as e:
        logger.warning("vocab_lint: proposta LLM falhou: %s", e)
        return _empty_proposal(f"proposta LLM falhou: {e}")

    if not isinstance(data, dict):
        return _empty_proposal("proposta LLM em formato inesperado")
    return {
        "novos_temas": data.get("novos_temas") or [],
        "sinonimos": data.get("sinonimos") or [],
        "sem_acao": bool(data.get("sem_acao", False)),
        "notas": data.get("notas") or "",
    }


# =============================================================================
# B3. Relatório (saída markdown)
# =============================================================================

def _fmt_evidence_table(title: str, agg: dict) -> list[str]:
    lines = [f"### {title}", ""]
    if not agg:
        lines += ["_(nenhuma)_", ""]
        return lines
    lines += ["| variação | count | fontes | exemplos |",
              "| --- | --- | --- | --- |"]
    for variacao, v in agg.items():
        exemplos = ", ".join(v["exemplos"][:5])
        fontes = ", ".join(v["fontes"])
        lines.append(f"| {variacao} | {v['count']} | {fontes} | {exemplos} |")
    lines.append("")
    return lines


def _fmt_yaml_diff(novos_temas: list[dict], vocab: list[str]) -> list[str]:
    """Bloco yaml pronto-pra-colar no tema_vocab do WIKI.md §5.9 (vocab + novos)."""
    novos = [t.get("tema", "").strip() for t in novos_temas if t.get("tema")]
    if not novos:
        return ["_(sem novos temas a adicionar)_", ""]
    merged = list(vocab) + [n for n in novos if n not in vocab]
    lines = ["```yaml", "tema_vocab:"]
    for t in merged:
        marker = "   # <-- NOVO (proposto)" if t in novos and t not in vocab else ""
        lines.append(f'  - "{t}"{marker}')
    lines += ["```", ""]
    return lines


def _fmt_synonyms_diff(sinonimos: list[dict]) -> list[str]:
    if not sinonimos:
        return ["_(sem sinônimos a adicionar)_", ""]
    lines = ["```python",
             "# domain/vocabulary._SYNONYMS — acrescentar:"]
    for s in sinonimos:
        var = (s.get("variacao") or "").strip().lower()
        canon = (s.get("canonico") or "").strip().lower()
        if var and canon:
            lines.append(f'    "{var}": "{canon}",')
    lines += ["```", ""]
    return lines


def build_report(evidence: dict, proposal: dict | None) -> str:
    """Monta o markdown do relatório. `proposal=None` ⇒ relatório 'sem evidência'."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = [f"# Vocab lint — {ts}", ""]

    n_var = len(evidence["variacoes"])
    n_tl = len(evidence["tema_livre"])
    n_st = len(evidence["sem_tema"])

    lines += ["## 1. Sumário executivo", "",
          f"- Variações fora do vocab: **{n_var}**",
          f"- Temas livres (web): **{n_tl}**",
          f"- Oportunidades sem tema: **{n_st}**"]

    if proposal is None:
        lines += ["", "**Sem evidência** — nenhuma variação, tema_livre ou sem-tema "
              "coletada. LLM não foi chamada.", ""]
        return "\n".join(lines)

    n_novos = len(proposal.get("novos_temas") or [])
    n_sin = len(proposal.get("sinonimos") or [])
    if proposal.get("sem_acao") or (n_novos == 0 and n_sin == 0):
        lines += ["", "**Proposta: SEM AÇÃO** — nada cruzou a barra de evidência "
              "(>=3 independentes para tema novo; equivalência clara para sinônimo).", ""]
    else:
        lines += ["", f"**Proposta:** {n_novos} tema(s) novo(s), {n_sin} sinônimo(s).", ""]
    if proposal.get("notas"):
        lines += [f"> Notas da LLM: {proposal['notas']}", ""]

    # 2. Evidências
    lines += ["## 2. Evidências", ""]
    lines += _fmt_evidence_table("Variações fora do vocab", evidence["variacoes"])
    lines += _fmt_evidence_table("Temas livres (web)", evidence["tema_livre"])
    lines += ["### Oportunidades sem tema", ""]
    if evidence["sem_tema"]:
        lines += ["| id | título | descrição |", "| --- | --- | --- |"]
        for s in evidence["sem_tema"]:
            desc = (s["descricao"] or "").replace("\n", " ").replace("|", "\\|")
            title = (s["title"] or "").replace("|", "\\|")
            lines.append(f"| {s['id']} | {title} | {desc} |")
        lines.append("")
    else:
        lines += ["_(nenhuma)_", ""]

    # 3. Proposta
    lines += ["## 3. Proposta (justificativas)", ""]
    if proposal.get("novos_temas"):
        lines += ["### Novos temas", ""]
        for t in proposal["novos_temas"]:
            ev = ", ".join(t.get("evidencia") or [])
            lines += [f"- **{t.get('tema', '?')}** (n={t.get('n', '?')}): "
                  f"{t.get('justificativa', '')}",
                  f"  - evidência: {ev}"]
        lines.append("")
    if proposal.get("sinonimos"):
        lines += ["### Sinônimos", ""]
        for s in proposal["sinonimos"]:
            ev = ", ".join(s.get("evidencia") or [])
            lines += [f"- **{s.get('variacao', '?')}** → `{s.get('canonico', '?')}`: "
                  f"{s.get('justificativa', '')}",
                  f"  - evidência: {ev}"]
        lines.append("")
    if not proposal.get("novos_temas") and not proposal.get("sinonimos"):
        lines += ["_(sem itens propostos)_", ""]

    # 4. Diff pronto-pra-aplicar
    lines += ["## 4. Diff pronto-para-aplicar", "",
          "### WIKI.md §5.9 — bloco `tema_vocab` completo (substituir):", ""]
    lines += _fmt_yaml_diff(proposal.get("novos_temas") or [], evidence["vocab"])
    lines += ["### domain/vocabulary.py — `_SYNONYMS`:", ""]
    lines += _fmt_synonyms_diff(proposal.get("sinonimos") or [])

    # 5. Checklist de aplicação
    lines += ["## 5. Checklist de aplicação", "",
          "1. [ ] Editar `WIKI.md` §5.9 (o doc é o DONO do vocabulário) com o "
          "bloco `tema_vocab` acima.",
          "2. [ ] Se houver sinônimos: acrescentar as entradas em "
          "`domain/vocabulary._SYNONYMS`.",
          "3. [ ] `python -m pytest tests/test_wiki_schema_consistency.py` "
          "(doc × código não divergem).",
          "4. [ ] Rebuild: `python pipeline/build_knowledge_graph.py`.",
          "5. [ ] Gate de não-regressão: `python -m core.eval matching --no-push` "
          "e a suíte `opportunity_type`.",
          ""]
    return "\n".join(lines)


def write_report(report: str, out_dir, *, ts: str | None = None):
    """Grava o relatório em `<out_dir>/vocab_lint_<ts>.md`. Cria o dir. Retorna o path."""
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"vocab_lint_{ts}.md"
    path.write_text(report, encoding="utf-8")
    return path


# =============================================================================
# Orquestração + CLI
# =============================================================================

def run(*, out_dir=DEFAULT_OUT_DIR, dry_run: bool = False) -> dict:
    """Roda o lint completo. Retorna {evidence, proposal, report_path}.

    - dry_run: só coleta + imprime evidência, sem LLM e sem relatório.
    - sem evidência: grava relatório 'sem evidência' e NÃO chama a LLM.
    """
    evidence = collect_evidence()
    n_var = len(evidence["variacoes"])
    n_tl = len(evidence["tema_livre"])
    n_st = len(evidence["sem_tema"])
    print(f"vocab_lint: {n_var} variações fora do vocab, {n_tl} tema_livre, "
          f"{n_st} sem-tema.")

    if dry_run:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return {"evidence": evidence, "proposal": None, "report_path": None}

    if not has_evidence(evidence):
        report = build_report(evidence, None)
        path = write_report(report, out_dir)
        print(f"vocab_lint: sem evidência — relatório em {path}")
        return {"evidence": evidence, "proposal": None, "report_path": path}

    proposal = propose(evidence)
    report = build_report(evidence, proposal)
    path = write_report(report, out_dir)
    # Sumário no stdout (as primeiras linhas até a 1ª seção de evidências).
    print("\n".join(report.split("\n## 2.")[0].splitlines()))
    print(f"\nvocab_lint: relatório completo em {path}")
    return {"evidence": evidence, "proposal": proposal, "report_path": path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vocab lint do tema_vocab (§5.9).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Só coleta e imprime evidência (sem LLM, sem relatório).")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR),
                        help="Diretório de saída dos relatórios.")
    args = parser.parse_args(argv)
    run(out_dir=args.out, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    # CLIs fora do backend/worker não têm o .env carregado — sem isto as chaves
    # não chegam e a proposta degrada pra no-op (gotcha conhecido do repo).
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
