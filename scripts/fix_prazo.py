#!/usr/bin/env python3
"""
Corrige o campo `prazo` em hipergrados com data não-parseável.

Para cada hipergrafo cujo `prazo` da Oportunidade não é uma data válida,
consulta o texto fonte (silver structured_docs) via LLM (gpt-4o-mini) para
extrair APENAS a data-limite de submissão.

Usage:
    python scripts/fix_prazo.py                      # corrige todos
    python scripts/fix_prazo.py --dry-run             # só lista, não altera
    python scripts/fix_prazo.py --id finep:745        # um específico
    python scripts/fix_prazo.py --source finep        # só uma fonte
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import KNOWLEDGE_GRAPH_DIR, SILVER_DIR
from core.llm.llm_client import make_client

HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"
STRUCTURED_DOCS_DIR = SILVER_DIR / "structured_docs"

MODEL = os.environ.get("FIX_PRAZO_MODEL", "gpt-4o-mini")

_DEADLINE_FORMATS = (
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d.%m.%y",
    "%d/%m/%y",
)

_CLASSIFY_PROMPT = """\
Analise as datas abaixo (cada uma com contexto) e responda APENAS qual delas
é a DATA LIMITE DE SUBMISSÃO de propostas deste edital.

Ignore nomes de programas ("Fluxo Contínuo" é um nome de programa, não indica
ausência de prazo). Ignore prazos de execução, vigência, resultado.

CANDIDATOS:
{candidates}

Responda APENAS a data DD/MM/AAAA, ou "null".
"""

_NO_DATE_PROMPT = """\
Extraia a data limite de SUBMISSÃO de propostas deste edital.
Se houver data explícita, responda DD/MM/AAAA. Se não, "null".

TEXTO:
{text}
"""

# Regex for deadline patterns — no LLM needed for these
_DEADLINE_KEYWORD_RE = re.compile(
    r"(?:data\s+limite|prazo\s+(?:final|para\s+envio|para\s+submissão|limite)|"
    r"submissão\s+(?:de\s+propostas|de\s+projetos|até)|"
    r"envio\s+(?:de\s+propostas|de\s+projetos|até))\s*"
    r"[^.]{0,80}?"
    r"(\d{2}/\d{2}/\d{4})",
    re.I,
)


def parse_date(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in _DEADLINE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Portuguese written dates: "16 de maio de 2024"
    m = re.match(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", s, re.I)
    if m:
        meses = {
            "janeiro": 1,
            "fevereiro": 2,
            "março": 3,
            "marco": 3,
            "abril": 4,
            "maio": 5,
            "junho": 6,
            "julho": 7,
            "agosto": 8,
            "setembro": 9,
            "outubro": 10,
            "novembro": 11,
            "dezembro": 12,
        }
        mo = meses.get(m.group(2).lower())
        if mo:
            try:
                return datetime(int(m.group(3)), mo, int(m.group(1)))
            except ValueError:
                return None
    return None


def _full_silver_text(edital_id: str, source: str) -> str:
    """Carrega o texto completo de um silver JSONL."""
    silver_path = STRUCTURED_DOCS_DIR / source / f"{edital_id}.jsonl"
    if not silver_path.exists():
        return ""
    blocks: list[str] = []
    with open(silver_path) as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            txt = (rec.get("text") or "").strip()
            if txt:
                blocks.append(txt)
    return "\n".join(blocks)


_CXT_WINDOW = 300

def _date_candidates(text: str) -> list[tuple[str, str]]:
    """Encontra datas dd/mm/aaaa no texto com contexto ao redor.
    Retorna [(data_str, contexto)]."""
    candidates: list[tuple[str, str]] = []
    seen = set()
    for m in re.finditer(r"\b(\d{2}/\d{2}/\d{4})\b", text):
        raw = m.group(1)
        if raw in seen:
            continue
        seen.add(raw)
        start = max(0, m.start() - _CXT_WINDOW)
        end = min(len(text), m.end() + _CXT_WINDOW)
        ctx = text[start:end].replace("\n", " ")
        candidates.append((raw, ctx))
    return candidates


def find_problematic_hypergraphs(
    source_filter: str | None = None,
) -> list[tuple[str, str, str, str]]:
    """Retorna (file_stem, source, native_id, current_prazo) para cada hipergrafo
    com prazo não-parseável."""
    results: list[tuple[str, str, str, str]] = []
    for path in sorted(HYPERGRAPHS_DIR.glob("*.json")):
        if ".bak" in path.name:
            continue
        # catálogos (ict, investidores, programas) não são editais
        stem = path.stem
        if "__" not in stem:
            continue
        source, _, native = stem.partition("__")
        if source_filter and source != source_filter:
            continue
        try:
            hg = json.loads(path.read_text())
        except Exception:
            continue
        for n in hg.get("nodes", []):
            if n.get("type") == "Oportunidade" and n.get("kind") == "edital":
                prazo = n.get("prazo") or ""
                if prazo and not parse_date(prazo):
                    results.append((stem, source, native, prazo))
                break
    return results


def _llm_extract(client, prompt: str) -> str | None:
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=30,
        )
        raw = (resp.choices[0].message.content or "").strip().strip('"').strip()
        if raw.lower() in ("null", "none", "", "não encontrada"):
            return None
        dt = parse_date(raw)
        if dt:
            return dt.strftime("%d/%m/%Y")
        return None
    except Exception as e:
        print(f"    LLM error: {e}")
        return None


def fix_via_llm(client, text: str) -> str | None:
    """Extrai data-limite de submissão do texto completo.
    1. Tenta regex por padrões de deadline (determinístico, sem LLM).
    2. Se não achar, usa LLM com candidatos a data.
    3. Se não houver datas, envia raw text como fallback."""
    # Passo 1: regex direto
    m = _DEADLINE_KEYWORD_RE.search(text)
    if m:
        return m.group(1)

    # Passo 2: LLM com candidatos
    candidates = _date_candidates(text)
    if candidates:
        lines = "\n".join(
            f"Data: {d}\nContexto: ...{c[:250]}..." for d, c in candidates
        )
        return _llm_extract(client, _CLASSIFY_PROMPT.format(candidates=lines))

    # Passo 3: LLM com raw text
    return _llm_extract(client, _NO_DATE_PROMPT.format(text=text[:6000]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Corrige prazo em hipergrados")
    parser.add_argument("--dry-run", action="store_true", help="Só lista, não altera")
    parser.add_argument("--id", help="Edital específico (ex: finep:745)")
    parser.add_argument("--source", help="Filtrar por fonte (ex: finep, fapesc)")
    args = parser.parse_args()

    client = make_client() if not args.dry_run else None

    problems = find_problematic_hypergraphs(source_filter=args.source)

    # Filtra por --id se fornecido
    if args.id:
        src, _, nid = args.id.partition(":")
        problems = [
            (s, src, nid, p) for s, src_, nid_, p in problems if f"{src_}:{nid_}" == args.id
        ]

    if not problems:
        print("Nenhum hipergrafo com prazo problemático encontrado.")
        return 0

    print(f"Encontrados {len(problems)} hipergrafos com prazo inválido:\n")
    for _stem, src, nid, p in problems:
        print(f'  {src}:{nid}  →  atual: "{p}"')

    if args.dry_run:
        print("\n[Dry-run] Nenhuma alteração feita.")
        return 0

    print()

    successes = 0
    failures = 0
    for stem, src, nid, _current_prazo in problems:
        print(f"  [{src}:{nid}] Consultando LLM...", end=" ")
        sys.stdout.flush()

        text = _full_silver_text(nid, src)
        if not text:
            print(f"SKIP — texto fonte não encontrado em {STRUCTURED_DOCS_DIR / src}")
            continue

        new_prazo = fix_via_llm(client, text)
        if new_prazo is None:
            print("null — mantendo atual")
            continue

        # Atualiza o hipergrafo
        path = HYPERGRAPHS_DIR / f"{stem}.json"
        try:
            hg = json.loads(path.read_text())
        except Exception as e:
            print(f"ERRO lendo JSON: {e}")
            failures += 1
            continue

        updated = False
        for n in hg.get("nodes", []):
            if n.get("type") == "Oportunidade" and n.get("kind") == "edital":
                old = n.get("prazo", "")
                n["prazo"] = new_prazo
                updated = True
                print(f"{old} → {new_prazo}")
                break

        if updated:
            # Backup
            bak = path.with_suffix(".json.bak")
            if not bak.exists():
                path.rename(bak)
            path.write_text(json.dumps(hg, ensure_ascii=False, indent=2))
            successes += 1
        else:
            print("ERRO — nó Oportunidade edital não encontrado")
            failures += 1

    print(f"\nConcluído. Sucessos: {successes}, falhas: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
