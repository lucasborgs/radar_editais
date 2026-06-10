"""Feeder DOU (Imprensa Nacional / INLABS) — torneira de descoberta Fase A.

Gera candidatos de ALTA PRECISÃO para a Descoberta a partir do Diário Oficial da
União, em vez da busca livre (Tavily). Produz `SearchHit`-equivalentes que entram
no MESMO `discover_opportunities()` (triagem LLM → extração → web_raw); o DOU é só
um segundo GERADOR de candidatos, paralelo ao Tavily. Não é um adapter/fonte: o
texto vai pro bronze `web` e o adapter `web` o chunka (spec_dou_feeder.md).

Fluxo:
  login INLABS (logar.php, email+password → cookie) → baixa o zip da seção do dia
  (DO1 normativo / DO3 avisos+chamamentos) → parseia cada matéria (XML) → pré-filtro
  determinístico (dropa licitação/resultado; casa fomento) → SearchHit.

Por que pré-filtro determinístico: o DO3 tem ~2900 matérias/dia, dominadas por
ruído de licitação. O pré-filtro corta pra ~dezenas ANTES de gastar LLM; a
precisão de persona (deep-tech vs edital acadêmico do MEC) fica com a triagem LLM
já existente em opportunity_discovery (recall-first aqui, precision lá).

Degrada sem levantar: sem credencial → []; 502 transitório do login → retry;
falha de rede → [] (logando). Credenciais: INLABS_EMAIL / INLABS_PASSWORD.
"""
from __future__ import annotations

import io
import logging
import os
import re
import time
import zipfile
from datetime import date

from core.web_search import SearchHit

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://inlabs.in.gov.br/logar.php"
_DL_URL = "https://inlabs.in.gov.br/index.php"
_UA = "radar-editais/0.1 (+descoberta DOU)"

# Seções do DOU relevantes a fomento: DO3 (avisos, chamamentos, editais) e DO1
# (atos normativos — algumas chamadas FINEP/MCTI saem aqui). DO2 = pessoal, fora.
DEFAULT_SECTIONS = ("DO3", "DO1")

# --- Pré-filtro determinístico (validado em DO3 2026-06-09: 2895 → ~117) -------
# artType cujo PREFIXO/substring indica ruído de licitação/resultado/pessoal —
# nunca é oportunidade de fomento aberta. Casado em lower().
_ARTTYPE_DROP = (
    "contrato", "licita", "pregão", "pregao", "aditivo", "homologa", "adjudica",
    "distrato", "diploma", "penalidade", "apostila", "ata de registro", "dispensa",
    "inexigibilidade", "ratifica", "resultado", "julgamento", "processo seletivo",
    "convocação", "convocacao", "credenciamento", "termo de fomento",
)
# artCategory (órgão) cujo topo indica claramente compras/licitação — dropa.
_ORGTOP_DROP = ("licita", "compras")
# Drop por TÍTULO (Identifica): mata RESULTADOS/contratos (extrato de termo/
# contrato/fomento, termo de fomento) que o artType genérico ("Extrato") não
# pega. Preserva "extrato de edital" (anúncio de chamada aberta) de propósito.
_IDENT_DROP_RE = re.compile(
    r"extrato d[eo] (termo|contrato|distrato|fomento|conv[êe]nio)|"
    r"\btermo de fomento\b|resultado (de|do) julgamento",
    re.I,
)
# Padrões que indicam oportunidade de fomento/chamada aberta (em Identifica+Ementa).
_KEEP_RE = re.compile(
    r"chamad[ao]|sele[çc][ãa]o p[úu]blica|chamamento|subven[çc]|fomento|"
    r"inova[çc][ãa]o|edital n|pesquisa e desenvolvimento|fundo|bolsa",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).strip()


# =============================================================================
# Sessão INLABS
# =============================================================================

def _login(session, *, retries: int = 4) -> bool:
    """Autentica no INLABS. True se obteve cookie de sessão. O login às vezes
    devolve 502 (manutenção transitória do handler) — daí o retry."""
    email = os.getenv("INLABS_EMAIL")
    pwd = os.getenv("INLABS_PASSWORD")
    if not email or not pwd:
        logger.warning("DOU: INLABS_EMAIL/INLABS_PASSWORD ausentes — feeder inativo")
        return False
    for i in range(retries):
        try:
            session.post(_LOGIN_URL, data={"email": email, "password": pwd}, timeout=30)
        except Exception as e:
            logger.warning("DOU: erro de rede no login (%s): %s", i, e)
        if session.cookies.get("inlabs_session_cookie"):
            return True
        time.sleep(3)
    logger.warning("DOU: login falhou após %d tentativas (502 sustentado?)", retries)
    return False


def _download_section(session, day: date, section: str) -> bytes | None:
    """Baixa o zip de uma seção (ex.: DO3) de um dia. None se indisponível
    (fim de semana, edição não publicada, erro)."""
    ds = day.isoformat()
    url = f"{_DL_URL}?p={ds}&dl={ds}-{section}.zip"
    try:
        r = session.get(url, timeout=120)
    except Exception as e:
        logger.warning("DOU: erro baixando %s-%s: %s", ds, section, e)
        return None
    if r.status_code == 200 and r.content[:2] == b"PK":
        return r.content
    logger.info("DOU: %s-%s indisponível (status=%s)", ds, section, r.status_code)
    return None


# =============================================================================
# Parse + filtro
# =============================================================================

def _iter_articles(zip_bytes: bytes):
    """Itera as matérias de um zip do INLABS como dicts já achatados."""
    import xml.etree.ElementTree as ET
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    for name in zf.namelist():
        if not name.lower().endswith(".xml"):
            continue
        try:
            with zf.open(name) as fx:
                root = ET.parse(fx).getroot()
        except Exception as e:
            logger.debug("DOU: XML ilegível %s: %s", name, e)
            continue
        art = root.find(".//article")
        if art is None:
            continue
        yield {
            "artType": art.get("artType") or "",
            "artCategory": art.get("artCategory") or "",
            "pubDate": art.get("pubDate") or "",
            "pdfPage": art.get("pdfPage") or "",
            "idMateria": art.get("idMateria") or "",
            "identifica": _strip_html(root.findtext(".//Identifica") or ""),
            "ementa": _strip_html(root.findtext(".//Ementa") or ""),
            "texto": _strip_html(root.findtext(".//Texto") or ""),
        }


def _is_candidate(a: dict, org_allowlist: tuple[str, ...] | None) -> bool:
    """Pré-filtro determinístico: dropa ruído de licitação/resultado, exige sinal
    de fomento. `org_allowlist` (opcional) restringe por topo do órgão — alavanca
    de precisão de persona/custo (None = recall-first, triagem LLM decide)."""
    at = a["artType"].lower()
    if any(d in at for d in _ARTTYPE_DROP):
        return False
    orgtop = a["artCategory"].split("/")[0]
    if any(d in orgtop.lower() for d in _ORGTOP_DROP):
        return False
    if _IDENT_DROP_RE.search(a["identifica"]):
        return False
    if org_allowlist and not any(o.lower() in orgtop.lower() for o in org_allowlist):
        return False
    return bool(_KEEP_RE.search(f"{a['identifica']} {a['ementa']} {at}"))


def _to_hit(a: dict) -> SearchHit:
    """Matéria DOU → SearchHit. `content` já é o Texto completo (sem full-fetch).
    A identidade (`url`) é a página PDF da matéria no in.gov.br. `agency` vem do
    topo do `artCategory` (órgão emissor confiável do XML — não deixa a triagem
    re-adivinhar).

    NOTA (validado por dry-run 2026-06-09): NÃO derivamos id estável por nº+órgão
    aqui — o "Nº N/ANO" do DOU é escopado por unidade(UASG)+tipo de ato, não por
    ministério, então `<órgão>-<nº>-<ano>` COLIDE em massa (ex.: 50+ atos distintos
    em `ministerio-da-educacao-1-2026`). Identidade de descoberta fica no url_hash
    (único por aviso); identidade de ciclo de vida é problema à parte (BACKLOG:
    'DOU — sync de ciclo de vida')."""
    url = a["pdfPage"] or f"https://www.in.gov.br/web/dou/-/{a['idMateria']}"
    snippet = a["ementa"] or a["texto"][:300]
    org = (a["artCategory"].split("/")[0] or "").strip()
    return SearchHit(title=a["identifica"] or "(sem título)", url=url,
                     snippet=snippet, content=a["texto"], agency=org)


# =============================================================================
# API pública
# =============================================================================

def dou_candidates(
    day: date | None = None,
    *,
    sections: tuple[str, ...] = DEFAULT_SECTIONS,
    org_allowlist: tuple[str, ...] | None = None,
) -> list[SearchHit]:
    """Candidatos de fomento do DOU de um dia (default: hoje), prontos pra entrar
    no `discover_opportunities()`. Lista vazia se sem credencial / indisponível /
    fim de semana — nunca levanta.

    `org_allowlist`: se passado, mantém só matérias cujo órgão-topo casa (ex.:
    ("Ministério da Ciência", "FINEP", ...)) — corta custo de triagem à custa de
    recall na cauda longa de FAPs. Default None = recall-first.
    """
    try:
        import requests
    except ImportError:
        logger.warning("DOU: requests ausente — feeder inativo")
        return []

    day = day or date.today()
    if day.weekday() >= 5:  # DOU ordinário não sai sáb/dom
        logger.info("DOU: %s é fim de semana — sem edição ordinária", day.isoformat())
        return []

    session = requests.Session()
    session.headers.update({"User-Agent": _UA})
    if not _login(session):
        return []

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for section in sections:
        blob = _download_section(session, day, section)
        if not blob:
            continue
        kept = 0
        for art in _iter_articles(blob):
            if not _is_candidate(art, org_allowlist):
                continue
            hit = _to_hit(art)
            if hit.url in seen:
                continue
            seen.add(hit.url)
            hits.append(hit)
            kept += 1
        logger.info("DOU: %s-%s → %d candidatos", day.isoformat(), section, kept)
    return hits


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import sys

    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    out = dou_candidates(d)
    print(f"\nDOU {d.isoformat()}: {len(out)} candidatos de fomento\n")
    for h in out[:25]:
        print(f"  [{h.title[:62]:62}] {h.url[:50]}")
