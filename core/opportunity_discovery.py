"""Engine de descoberta de oportunidades (item 2.2).

Varre a web por editais/chamadas/desafios de fomento, tria (é oportunidade real?),
extrai campos e grava no bronze da fonte `web`. A Descoberta é a TORNEIRA
AUTOMÁTICA da fonte web (a outra é a seed list manual em `web_sources`): não tem
mais bronze/índice próprios. Os registros entram em `web_raw/` com
`verificacao=provisorio` e, daí pra frente, são páginas web como quaisquer
outras — `build_knowledge_graph` os ingere via `_build_editais("web")` e o
adapter `pipeline.adapters.web` os chunka pro RAG (Opção A, WIKI.md §12.4).

Pipeline:
  queries (wikis/_discovery.md) → web_search (Tavily) → dedup (ledger + KG)
    → triagem (LLM barato: é fomento? agência?) → extração (LLM capaz: campos)
    → full-fetch do texto da página → web_raw/web_discovery_*.json + ledger

Custo: triagem roda em muitos candidatos (modelo barato); extração só nos que
passaram (modelo capaz). Nada entra no KG aqui — isso é o build, e tudo provisório.
Funções de LLM/busca isoladas para teste com mocks.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from config import BRONZE_DIR
from core import kg_store
from core import web_search as websearch
from core import wiki_schema as ws
from core.web_identity import normalize_web_url, web_url_hash

logger = logging.getLogger(__name__)

# Bronze de saída: o MESMO da fonte web (a Descoberta é uma torneira dela).
_WEB_BRONZE_DIR = BRONZE_DIR / "web_raw"
# Estado da Descoberta (ledger de dedup cross-execução) — fica num dir próprio,
# não em web_raw, para não se confundir com os arquivos bronze. O dotfile já
# seria ignorado pelo glob("*.json"), mas mantê-lo à parte é mais claro.
_DISCOVERY_STATE_DIR = BRONZE_DIR / "discovery_raw"
_LEDGER = _DISCOVERY_STATE_DIR / ".ledger.json"

# Cap defensivo do texto guardado por página (o chunker re-fatia depois). Páginas
# de fomento ficam bem abaixo; evita um caso patológico inflar o bronze.
_TEXTO_CRU_CAP = 60_000


# =============================================================================
# LLM client (triagem barata / extração capaz) — padrão de core.content_library
# =============================================================================

def _make_client(role: str):
    """(client, model) para 'triage' (barato) ou 'extract' (capaz). None se sem
    credencial — o caller degrada."""
    from core.llm_client import make_client
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    try:
        if backend == "gemini":
            client = make_client(
                api_key=os.environ["GEMINI_API_KEY"],
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            model = "gemini-2.5-flash" if role == "triage" else "gemini-2.5-pro"
            return client, model
        client = make_client(api_key=os.environ["OPENAI_API_KEY"])
        if role == "triage":
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        else:
            model = os.getenv("OPENAI_MODEL_PRO", "gpt-4o")
        return client, model
    except KeyError:
        return None, None


def _json_from_llm(client, model, system: str, user: str, max_tokens: int = 1200) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0,
        max_tokens=max_tokens,
    )
    raw = resp.choices[0].message.content.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
    return json.loads(raw)


# =============================================================================
# Triagem + extração (1 candidato cada)
# =============================================================================

_TRIAGE_SYSTEM = (
    "Você tria resultados de busca para saber se são OPORTUNIDADES DE FOMENTO À "
    "INOVAÇÃO vigentes no Brasil (edital, chamada pública, subvenção, desafio com "
    "inscrição aberta). NÃO conta: notícia, blog, artigo, página institucional "
    "genérica, oportunidade encerrada. Responda só JSON: "
    '{"is_opportunity": true|false, "agency": "sigla/nome curto da agência ou \\"\\""}.'
)


def _triage(hit: websearch.SearchHit, client, model) -> dict:
    """Classifica um resultado de busca. Falha → descarta (is_opportunity=False)."""
    try:
        data = _json_from_llm(
            client, model, _TRIAGE_SYSTEM,
            f"Título: {hit.title}\nURL: {hit.url}\nTrecho: {hit.snippet}",
            max_tokens=200,
        )
        return {"is_opportunity": bool(data.get("is_opportunity")),
                "agency": (data.get("agency") or "").strip()}
    except Exception as e:
        logger.warning("triagem falhou (%s): %s", hit.url, e)
        return {"is_opportunity": False, "agency": ""}


def _extract(hit: websearch.SearchHit, page_text: str, agency: str, client, model) -> dict | None:
    """Extrai campos no schema comum a partir do texto da página. None se falhar.

    Temas restritos ao vocab canônico §5.9 (a defesa final fica no normalizador
    do build, mas pedir o vocab aqui melhora o recall da ponte)."""
    vocab = ws.tema_vocab()
    system = (
        "Extraia os campos de uma oportunidade de fomento a partir do texto. "
        "Responda só JSON com as chaves: titulo, prazo_envio (dd/mm/yyyy ou \"\"), "
        "publico_alvo, descricao (2-3 frases), status (ABERTA|ENCERRADA|\"\"), "
        "tema (lista; ESCOLHA só desta lista canônica, [] se nenhum servir: "
        f"{vocab}). Não invente dados que não estão no texto."
    )
    try:
        data = _json_from_llm(
            client, model, system,
            f"Título: {hit.title}\nURL: {hit.url}\n\nTEXTO:\n{page_text[:6000]}",
        )
    except Exception as e:
        logger.warning("extração falhou (%s): %s", hit.url, e)
        return None

    tema = data.get("tema") or []
    if isinstance(tema, str):
        tema = [tema]
    # Registro no SCHEMA DA FONTE WEB (não mais um schema de discovery próprio):
    # url/url_hash dão a identidade `web:<url_hash>` (mesma de páginas manuais);
    # texto_cru é o corpo pro chunking; verificacao=provisorio marca a origem
    # automática. `agency` sobrevive como campo (futura graduação Fase C).
    return {
        "url": normalize_web_url(hit.url),
        "url_hash": web_url_hash(hit.url),
        "title": data.get("titulo") or hit.title,
        "texto_cru": (page_text or "")[:_TEXTO_CRU_CAP],
        "prazo_envio": data.get("prazo_envio", ""),
        "publico_alvo": data.get("publico_alvo", ""),
        "descricao": data.get("descricao", ""),
        "status": data.get("status", "") or "ABERTA",
        "tema": "; ".join(t for t in tema if isinstance(t, str)),
        "agency": agency or "",
        "fonte": agency or "Web (descoberta)",
        "verificacao": "provisorio",
        "data_extracao": datetime.now(timezone.utc).date().isoformat(),
    }


# =============================================================================
# Ledger (dedup cross-execução) — file-based MVP
# =============================================================================

def _norm_url(url: str) -> str:
    return (url or "").split("#")[0].rstrip("/").lower()


def _load_ledger() -> set[str]:
    if _LEDGER.exists():
        try:
            return set(json.loads(_LEDGER.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_ledger(urls: set[str]) -> None:
    _DISCOVERY_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _LEDGER.write_text(json.dumps(sorted(urls), ensure_ascii=False, indent=2),
                       encoding="utf-8")


def _known_urls() -> set[str]:
    """URLs já vistas: ledger ∪ links de editais já no KG (não re-descobrir)."""
    known = _load_ledger()
    idx = kg_store.load_index()
    known |= {_norm_url(e.get("link", "")) for e in idx.get("editais", [])}
    return known


# =============================================================================
# Orquestração
# =============================================================================

def discover_opportunities(*, write: bool = True) -> list[dict]:
    """Roda o ciclo de descoberta e retorna os registros bronze produzidos.

    Idempotente entre execuções via ledger (URL normalizada). Sem LLM/busca
    disponível, degrada para [] (logando) — não levanta.
    """
    cfg = ws.discovery_config()
    queries = cfg.get("queries", [])
    k = int(cfg.get("max_results_per_query", 8))
    max_cand = int(cfg.get("max_candidates", 40))
    if not queries:
        logger.warning("descoberta: sem queries em wikis/_discovery.md")
        return []

    known = _known_urls()
    seen_now: set[str] = set()
    candidates: list[websearch.SearchHit] = []
    for q in queries:
        if len(candidates) >= max_cand:
            break
        try:
            hits = websearch.web_search(q, k=k)
        except Exception as e:
            logger.warning("busca falhou (%s): %s", q, e)
            continue
        for h in hits:
            nu = _norm_url(h.url)
            if not nu or nu in known or nu in seen_now:
                continue
            seen_now.add(nu)
            candidates.append(h)
            if len(candidates) >= max_cand:
                break

    if not candidates:
        logger.info("descoberta: nenhum candidato novo")
        return []

    tri_client, tri_model = _make_client("triage")
    ext_client, ext_model = _make_client("extract")
    if tri_client is None or ext_client is None:
        logger.warning("descoberta: sem credencial LLM — abortando triagem/extração")
        return []

    records: list[dict] = []
    for h in candidates:
        verdict = _triage(h, tri_client, tri_model)
        if not verdict["is_opportunity"]:
            continue
        page_text = _page_text(h)
        rec = _extract(h, page_text, verdict["agency"], ext_client, ext_model)
        if rec:
            records.append(rec)

    if write and records:
        _WEB_BRONZE_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Prefixo `web_discovery_` distingue a torneira automática da manual
        # (`web_scan_`) dentro do mesmo bronze; ambas são unidas por url_hash.
        out = _WEB_BRONZE_DIR / f"web_discovery_{ts}.json"
        out.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        _save_ledger(known | {_norm_url(r["url"]) for r in records})
        logger.info("descoberta: %d oportunidades → %s", len(records), out.name)

    return records


def _page_text(hit: websearch.SearchHit) -> str:
    """Texto da página para extração + chunking. Tenta o fetch COMPLETO (corpo
    inteiro — o `raw_content` do Tavily vem capado em ~2k chars, raso demais pro
    RAG); cai para o content capado e por fim o snippet. Nunca levanta."""
    try:
        from core.agent_tools.profile_tools import _fetch_and_parse
        full = (_fetch_and_parse(hit.url) or {}).get("text", "") or ""
        if len(full) > len(hit.content or ""):
            return full
    except Exception as e:
        logger.debug("descoberta: full-fetch falhou (%s): %s", hit.url, e)
    return hit.content or hit.snippet or ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = discover_opportunities()
    print(f"\nDescoberta: {len(out)} oportunidades provisórias extraídas.")
    for r in out[:10]:
        print(f"  [{r.get('fonte', 'web')}] {r['title'][:70]} — {r['url']}")
