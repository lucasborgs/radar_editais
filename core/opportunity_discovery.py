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
from datetime import datetime, timedelta, timezone

from config import BRONZE_DIR
from core import web_search as websearch
from core.kg import kg_store
from core.kg import wiki_schema as ws
from core.web_identity import normalize_web_url, web_url_hash

logger = logging.getLogger(__name__)

# Bronze de saída: o MESMO da fonte web (a Descoberta é uma torneira dela).
_WEB_BRONZE_DIR = BRONZE_DIR / "web_raw"
# Ledger LEGADO (file-based, pré-kg_store): mantido só como fonte de migração —
# o ledger vive no kg_store (`discovery_ledger`), durável em Postgres quando
# configurado (o FS do worker de prod é efêmero; sem isto, cada redeploy
# re-triava URLs já vistas).
_LEGACY_LEDGER = BRONZE_DIR / "discovery_raw" / ".ledger.json"

# Cap defensivo do texto guardado por página (o chunker re-fatia depois). Páginas
# de fomento ficam bem abaixo; evita um caso patológico inflar o bronze.
_TEXTO_CRU_CAP = 60_000


# =============================================================================
# LLM client (triagem barata / extração capaz) — padrão de core.services.content_library
# =============================================================================

def _make_client(role: str):
    """(client, model) para 'triage' (barato) ou 'extract' (capaz). None se sem
    credencial — o caller degrada."""
    from core.llm.llm_client import make_client
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
    "Você tria resultados para um radar de fomento voltado a STARTUPS DEEP-TECH. "
    "O texto costuma ser um AVISO curto, sem todos os detalhes — na DÚVIDA sobre o "
    "tema, APROVE; a análise profunda vem depois (a IA mostra, o humano decide). "
    "APROVE (is_opportunity=true) oportunidades ABERTAS de fomento, pesquisa, "
    "inovação ou desenvolvimento tecnológico (subvenção, chamada/edital de "
    "inovação, P&D, desafio tecnológico) — inovação em QUALQUER setor conta "
    "(biotec, agritech, healthtech, energia, defesa...). "
    "REJEITE (is_opportunity=false) SÓ quando for CLARAMENTE irrelevante a deep-"
    "tech: assistência social, cultura, esporte, saúde assistencial, agricultura "
    "familiar/merenda (PNAE/PAA), povos indígenas, credenciamento de prestadores, "
    "processo seletivo/concurso de pessoal, compra/licitação; ou ALTERAÇÃO/"
    "PRORROGAÇÃO/RESULTADO de chamada antiga, notícia, página institucional, "
    "oportunidade encerrada. "
    "REJEITE também quando a página NÃO é UMA chamada específica: página-lista/"
    "portal/agregador de editais ('editais abertos', 'oportunidades'), homepage "
    "de programa/agência, ou notícia que anuncia VÁRIOS editais de uma vez — "
    "essas páginas não viram um registro útil (1 URL = 1 oportunidade). "
    "Responda só JSON: "
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
        "opportunity_type (UM de: edital|desafio|programa — desafio=desafio "
        "tecnológico/open innovation de empresa-âncora; programa=aceleração/"
        "incubação/cohort; edital=chamada/edital de fomento público padrão), "
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
        # opportunity_type (Fase B): tipo-evento classificado pela LLM. Default
        # edital (chamada pública padrão); build_knowledge_graph o carrega ao índice.
        "opportunity_type": (data.get("opportunity_type") or "edital").strip().lower(),
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


# Domínios sociais: post de rede social ANUNCIA a oportunidade mas nunca É a
# página dela — conteúdo raso pro chunking e link errado no card. Dry-run de
# triagem 2026-06-10: 3 dos 28 aprovados eram Instagram. Drop determinístico
# ANTES da triagem (economiza a chamada). Página da agência continua entrando
# normalmente pelas queries.
_SOCIAL_DOMAINS = (
    "instagram.com", "facebook.com", "linkedin.com", "x.com", "twitter.com",
    "youtube.com", "tiktok.com",
)


def _is_social(url: str) -> bool:
    host = _norm_url(url).split("//")[-1].split("/")[0]
    return any(host == d or host.endswith("." + d) for d in _SOCIAL_DOMAINS)


def _load_ledger() -> set[str]:
    """Ledger via kg_store (blob `discovery_ledger`, {"urls": [...]}) ∪ ledger
    file-based legado, se existir — migração por união: o legado é absorvido no
    próximo _save_ledger e pode ser deletado depois."""
    try:
        urls = set(kg_store.load("discovery_ledger", default={}).get("urls", []))
    except Exception:
        urls = set()
    if _LEGACY_LEDGER.exists():
        try:
            urls |= set(json.loads(_LEGACY_LEDGER.read_text(encoding="utf-8")))
        except Exception:
            pass
    return urls


def _save_ledger(urls: set[str]) -> None:
    kg_store.save("discovery_ledger", {"urls": sorted(urls)})


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
    max_dou = int(cfg.get("max_dou_candidates", 80))
    if not queries:
        logger.warning("descoberta: sem queries em wikis/_discovery.md")
        return []

    known = _known_urls()
    seen_now: set[str] = set()
    candidates: list[websearch.SearchHit] = []

    # Gerador DOU (espinha de alta precisão, spec_dou_feeder.md §6) — ANTES do
    # Tavily e com ORÇAMENTO PRÓPRIO (max_dou_candidates): no 1º shadow-run
    # (2026-06-10) o DOU rendeu 63 candidatos/dia e, contando pro max_cand
    # compartilhado, ZEROU o Tavily — as zonas gap-filler (FAPs, desafios,
    # aceleradoras; §6.1) ficariam permanentemente sem cobertura. Atrás de flag
    # pra ligar/desligar em prod sem tocar o caminho Tavily (e desligar se o
    # INLABS cair). Busca o DOU de ONTEM (UTC): o cron roda 04:00 UTC, antes da
    # publicação da edição do dia (manhã BRT) — "hoje" seria sempre vazio; D-1 é
    # determinístico e o ledger mantém a idempotência.
    if os.getenv("DISCOVERY_DOU_ENABLED", "0") == "1":
        from core.dou_feeder import dou_candidates  # noqa: PLC0415
        day = datetime.now(timezone.utc).date() - timedelta(days=1)
        for h in dou_candidates(day):
            if len(candidates) >= max_dou:
                break
            nu = _norm_url(h.url)
            if nu and nu not in known and nu not in seen_now:
                seen_now.add(nu)
                candidates.append(h)
        logger.info("descoberta: %d candidatos DOU (%s)", len(candidates),
                    day.isoformat())

    # Tavily conta o próprio orçamento (max_candidates) — separado do DOU.
    n_dou = len(candidates)
    for q in queries:
        if len(candidates) - n_dou >= max_cand:
            break
        try:
            hits = websearch.web_search(q, k=k)
        except Exception as e:
            logger.warning("busca falhou (%s): %s", q, e)
            continue
        for h in hits:
            nu = _norm_url(h.url)
            if not nu or nu in known or nu in seen_now or _is_social(h.url):
                continue
            seen_now.add(nu)
            candidates.append(h)
            if len(candidates) - n_dou >= max_cand:
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
        # Prefere o órgão que a FONTE já conhece (ex.: DOU lê do artCategory) ao
        # palpite da triagem — só cai no palpite quando a fonte é cega (Tavily).
        agency = getattr(h, "agency", "") or verdict["agency"]
        rec = _extract(h, page_text, agency, ext_client, ext_model)
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
    RAG); cai para o content capado e por fim o snippet. Nunca levanta.

    Hits com `full_text` (DOU: `content` = Texto completo do XML) pulam o fetch —
    economiza 1 request/candidato e a URL (visualizador JSP) não renderia melhor."""
    if hit.full_text:
        return hit.content or hit.snippet or ""
    try:
        from core.llm.agent_tools.profile_tools import _fetch_and_parse
        full = (_fetch_and_parse(hit.url) or {}).get("text", "") or ""
        if len(full) > len(hit.content or ""):
            return full
    except Exception as e:
        logger.debug("descoberta: full-fetch falhou (%s): %s", hit.url, e)
    return hit.content or hit.snippet or ""


if __name__ == "__main__":
    # CLI do shadow-run (spec_dou_feeder §9): fora do backend/worker, ninguém
    # carregou o .env ainda — sem isto as chaves não chegam e tudo degrada
    # silenciosamente pra no-op.
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = discover_opportunities()
    print(f"\nDescoberta: {len(out)} oportunidades provisórias extraídas.")
    for r in out[:10]:
        print(f"  [{r.get('fonte', 'web')}] {r['title'][:70]} — {r['url']}")
