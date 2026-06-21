"""Produtor de `eligibility_constraints` na wiki page (spec edital-eligibility-constraints, PR3).

Materializa a elegibilidade DURA (região / idade da empresa / faturamento) que
hoje só vive como PROSA em `key_requirements` no campo TIPADO
`eligibility_constraints` que o schema reserva e o matcher já lê
(`hybrid_match_service._score_elegibilidade_dura`). Aditivo: escreve SÓ esse
campo no card, sem tocar objective/themes/key_requirements/etc.

Seam: passo de ENRIQUECIMENTO sobre a wiki page já sintetizada (D1, alternativa
explícita na spec), reusando o MESMO texto silver que gerou objective/
key_requirements (via `etl_process._load_documents_via_silver`). Desacoplado da
síntese de propósito: re-sintetizar com modelo free é flaky (JSON inválido) e
clobbera o card rico para `metadata_only`. Aqui só LEMOS o card e ANEXAMOS um
campo — nunca regeneramos o resto.

Modelo: o produtor roda 1×/edital, repetível sobre o catálogo → NÃO pode queimar
OpenAI. Backend resolvido por `ELIGIBILITY_BACKEND` (default `gemini` free-tier;
cai para `LLM_BACKEND`). Reusa o extrator tipado `core.edital_extractor`
(abstenção + evidence verbatim), forçando o backend free só nesta chamada.

Cache: por hash do texto (espelha `.enrichment_cache.json` da raiz) — não
re-chama LLM em edital com texto inalterado. Cards `metadata_only` (sem texto)
não têm o que extrair → pulados.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from config import ROOT

logger = logging.getLogger(__name__)

# Cache local espelhando `.enrichment_cache.json` da raiz: {edital_id: text_hash}.
ELIGIBILITY_CACHE_FILE = ROOT / ".eligibility_cache.json"

# Tipos aceitos pelo schema (EligibilityConstraint). cnae/consortium podem ser
# extraídos mas NÃO pontuam ainda no matcher (_CONSTRAINT_SCORERS cobre só
# region/company_age/revenue) — gravamos todos; o scorer ignora os não-suportados.
_SCHEMA_TYPES = {"region", "company_age", "revenue", "cnae", "consortium"}


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _load_cache() -> dict[str, str]:
    if not ELIGIBILITY_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(ELIGIBILITY_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    try:
        ELIGIBILITY_CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("Falha ao gravar cache de elegibilidade: %s", e)


def _resolve_backend() -> str:
    """Backend do PRODUTOR. Default `openai` (gpt-4o-mini, via _resolve_model).

    NÃO herda `LLM_BACKEND` — é um knob próprio (`ELIGIBILITY_BACKEND`) para que o
    produtor seja trocável de forma independente. Default openai porque o produtor é
    um passo de build one-time (custo em centavos) e o free-tier (gemini) esgota
    rápido — cap de ~20 req/dia/modelo não cobre o lote do catálogo. Para usar o
    free-tier/self-host quando viável, basta `ELIGIBILITY_BACKEND=gemini|ollama`."""
    return (os.getenv("ELIGIBILITY_BACKEND") or "openai").lower()


def _resolve_model(backend: str) -> str | None:
    """Modelo do PRODUTOR. `ELIGIBILITY_MODEL` sobrepõe. Senão, no backend openai
    usa o slot BARATO `OPENAI_MODEL` (gpt-4o-mini), NÃO o `OPENAI_MODEL_PRO` que o
    extrator escolhe por default — o produtor é um passo de build em lote, custo em
    agregado importa. Nos demais backends (gemini/ollama) retorna None → o extrator
    usa o default da fonte (GEMINI_MODEL / OLLAMA_MODEL)."""
    override = os.getenv("ELIGIBILITY_MODEL")
    if override:
        return override
    if backend == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return None


def extract_constraints(source: str, native_id: str, text: str) -> list[dict]:
    """Extrai `eligibility_constraints` (lista {type, description, evidence}) do
    texto do edital via o extrator tipado, no backend do PRODUTOR (free por default).

    Retorna [] em qualquer falha (soft/aditivo — nunca derruba o build).
    """
    backend = _resolve_backend()
    model = _resolve_model(backend)
    # Força o backend do extrator só nesta chamada (restaura depois) — não vaza
    # para outros consumidores de LLM_BACKEND no mesmo processo.
    prev = os.environ.get("LLM_BACKEND")
    os.environ["LLM_BACKEND"] = backend
    try:
        from core.edital_extractor import extract_edital
        extraction = extract_edital(source, native_id, text, model=model)
    except Exception as e:
        logger.warning("Extração de elegibilidade falhou para %s:%s — %s",
                       source, native_id, e)
        return []
    finally:
        if prev is None:
            os.environ.pop("LLM_BACKEND", None)
        else:
            os.environ["LLM_BACKEND"] = prev

    out: list[dict] = []
    for c in extraction.eligibility_constraints:
        ctype = (c.type or "").strip().lower()
        if ctype not in _SCHEMA_TYPES:
            continue
        state = getattr(c.state, "value", c.state) if c.state is not None else None
        if state == "absent":  # abstenção não vira constraint (anti-alucinação)
            continue
        out.append({"type": ctype, "description": c.description, "evidence": c.evidence})
    return out


# ---------------------------------------------------------------------------
# Orquestrador — enriquecimento sobre as wiki pages existentes
# ---------------------------------------------------------------------------

def _wiki_text(edital_id: str) -> str:
    """Texto silver do edital (o MESMO insumo da síntese). Vazio = sem texto."""
    from pipeline.etl_process import _load_documents_via_silver
    documents, _ = _load_documents_via_silver(edital_id)
    return "\n\n".join(t for _, t in documents)


def enrich_page(page: dict, *, edital_id: str | None = None,
                cache: dict[str, str] | None = None, force: bool = False) -> bool:
    """Anexa `eligibility_constraints` à wiki page (in-place), aditivo.

    Pula cards `metadata_only` (sem texto). Cache por hash do texto: hit → mantém
    o que o card já tem, sem chamar LLM (a menos de `force`).
    `edital_id` (prefixado) sobrepõe o `page["id"]` — cards antigos podem ter id
    sem prefixo de fonte; o caller deriva o id canônico do path.
    Retorna True se chamou o extrator (campo potencialmente atualizado).
    """
    if page.get("source") == "metadata_only":
        return False
    eid = edital_id or page.get("id", "")
    if not eid or ":" not in eid:
        return False  # sem id prefixado não dá para resolver fonte/silver
    text = _wiki_text(eid).strip()
    if not text:
        return False

    h = _text_hash(text)
    if (not force and cache is not None and cache.get(eid) == h
            and "eligibility_constraints" in page):
        return False  # texto inalterado e já populado → skip

    source, native_id = eid.split(":", 1)
    page["eligibility_constraints"] = extract_constraints(source, native_id, text)
    if cache is not None:
        cache[eid] = h
    return True


# Pausa entre chamadas LLM. O free-tier (gemini) tem RPM baixo; sem pacing o lote
# estoura o rate-limit. Ajustável por env (0 desliga, ex.: backend pago).
_CALL_DELAY = float(os.getenv("ELIGIBILITY_CALL_DELAY", "4.0"))


def run(edital_ids: list[str] | None = None, *, skip_cache: bool = False,
        persist_durable: bool = True) -> dict:
    """Enriquece as wiki pages locais com `eligibility_constraints`.

    Lê cada wiki page do disco (via kg_store/iter), extrai a elegibilidade dura
    do texto silver, e grava SÓ esse campo de volta no arquivo (aditivo). Por
    default também publica as páginas atualizadas no store durável (Postgres se
    configurado), espelhando o etl_process.

    `edital_ids`: subconjunto (já prefixado, ex.: `fapesp:18203`). None = todas.
    Retorna um resumo `{processed, with_constraint, skipped, by_type}`.
    """
    from core.kg import kg_store
    from core.kg.edital_id import iter_wiki_pages, wiki_page_path

    cache = {} if skip_cache else _load_cache()
    summary = {"processed": 0, "with_constraint": 0, "skipped": 0,
               "by_type": {}, "ids_with_constraint": []}
    updated_pages: dict[str, dict] = {}

    for page_path in iter_wiki_pages():
        try:
            page = json.loads(page_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Id canônico (prefixado) derivado do path: pasta=fonte, arquivo=native_id.
        # Robusto a cards antigos cujo `page["id"]` não tem prefixo de fonte.
        eid = f"{page_path.parent.name}:{page_path.stem}"
        if edital_ids and eid not in edital_ids:
            continue
        called = enrich_page(page, edital_id=eid, cache=cache, force=skip_cache)
        if not called:
            summary["skipped"] += 1
            # Declara o campo vazio em páginas puladas (metadata_only / sem texto /
            # cache hit já populado) que ainda não o carregam — mantém o schema
            # consistente (WIKI.md §4) sem chamar LLM. Só grava se faltava.
            if "eligibility_constraints" not in page:
                page["eligibility_constraints"] = []
                page_path.write_text(
                    json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
                updated_pages[eid] = page
            continue
        summary["processed"] += 1
        constraints = page.get("eligibility_constraints") or []
        if constraints:
            summary["with_constraint"] += 1
            summary["ids_with_constraint"].append(eid)
            for c in constraints:
                t = c.get("type", "?")
                summary["by_type"][t] = summary["by_type"].get(t, 0) + 1
        # Grava o arquivo local (aditivo) + acumula p/ o store durável.
        path = wiki_page_path(eid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
        updated_pages[eid] = page
        _save_cache(cache)
        if _CALL_DELAY:
            time.sleep(_CALL_DELAY)

    if persist_durable and updated_pages:
        try:
            kg_store.save_wiki_pages(updated_pages)
        except Exception as e:
            logger.warning("Falha ao publicar wiki pages no store durável: %s", e)
    return summary


def main() -> None:
    import argparse
    from dotenv import load_dotenv
    load_dotenv()  # credenciais antes dos imports que as leem (CLI standalone)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                        datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(
        description="Produtor de eligibility_constraints (enriquece wiki pages)")
    parser.add_argument("--edital", nargs="+", dest="edital_ids",
                        help="IDs específicos (prefixados, ex.: fapesp:18203)")
    parser.add_argument("--skip-cache", action="store_true",
                        help="Ignora o cache e re-extrai tudo")
    parser.add_argument("--no-durable", action="store_true",
                        help="Não publica no store durável (Postgres); só augmenta os "
                             "arquivos locais. Use para augmentar local sem tocar o store.")
    args = parser.parse_args()
    summary = run(edital_ids=args.edital_ids, skip_cache=args.skip_cache,
                  persist_durable=not args.no_durable)
    print("=" * 60)
    _backend = _resolve_backend()
    print(f"PRODUTOR DE ELEGIBILIDADE — backend: {_backend}  modelo: {_resolve_model(_backend) or '(default da fonte)'}")
    print(f"  processados: {summary['processed']}  | com constraint: {summary['with_constraint']}"
          f"  | pulados (metadata_only/sem texto): {summary['skipped']}")
    print(f"  por tipo: {summary['by_type']}")
    print(f"  ids com constraint: {summary['ids_with_constraint']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
