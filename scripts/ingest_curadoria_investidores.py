#!/usr/bin/env python3
"""Ingestão da curadoria externa (Parte B1) → investidores.json.

Os 11 fundos existentes foram curados à mão (verificado_em 2026-06-09) e são
AUTORITATIVOS — não os sobrescrevemos. Esta ingestão só ADICIONA os fundos
genuinamente novos vindos da curadoria-LLM (curadoria.md, Prompt B1). Cada novo:
  - valida tese_themes ⊆ vocab canônico, setores ⊆ setor_vocab, estagio ⊆ estagio_vocab
  - se generalista=true, força tese_themes=[] (invariante do schema)
  - verificado_em=None (single-source LLM, AINDA não verificado por humano)
Rebuilda themes_index + total_investidores + last_updated. Idempotente (skip por id).
"""
from __future__ import annotations

import datetime as _dt

from core.kg import kg_store
from core.kg import wiki_schema as ws

# Novos fundos da curadoria (ChatGPT, Prompt B1) que NÃO estão nos 11 existentes.
# URLs já limpas do markdown. verificado_em deixado None de propósito (não-verificado).
NEW_FUNDS = [
    {
        "id": "investidor:maya-capital", "name": "MAYA Capital",
        "tese": "Investe na primeira rodada institucional de startups latino-americanas de alto potencial; generalista com foco em tecnologia e transformação de mercados.",
        "tese_themes": [], "tese_keywords": ["software", "internet", "b2b", "b2c", "marketplaces", "fintech"],
        "setores": ["multissetorial"], "estagio_alvo": ["pre-seed", "seed"],
        "ticket_range": None, "lead_follow": "lead", "generalista": True,
        "anti_tese": "Não foca em estágios avançados de crescimento como principal estratégia.",
        "fund_status": "ativo", "site": "https://www.maya.capital",
        "source_urls": ["https://www.maya.capital/about-us"], "verificado_em": None,
    },
    {
        "id": "investidor:norte-ventures", "name": "Norte Ventures",
        "tese": "Investe em startups tecnológicas brasileiras em estágio inicial com potencial de crescimento relevante; multissetorial.",
        "tese_themes": [], "tese_keywords": ["software", "b2b", "saas", "marketplaces", "tech"],
        "setores": ["multissetorial"], "estagio_alvo": ["pre-seed", "seed"],
        "ticket_range": None, "lead_follow": "ambos", "generalista": True,
        "anti_tese": "Não possui foco temático exclusivo em um vertical específico.",
        "fund_status": "ativo", "site": "https://norte.ventures",
        "source_urls": ["https://norte.ventures"], "verificado_em": None,
    },
    {
        "id": "investidor:oria-capital", "name": "Oria Capital",
        "tese": "Investe em empresas de tecnologia B2B com software escalável e crescimento acelerado; prioriza produtos já validados.",
        "tese_themes": ["tecnologias digitais e conectividade"],
        "tese_keywords": ["b2b", "software", "saas", "enterprise software", "cloud"],
        "setores": ["ti-software"], "estagio_alvo": ["serie-a", "growth"],
        "ticket_range": None, "lead_follow": "lead", "generalista": False,
        "anti_tese": "Não foca em negócios não tecnológicos ou em estágio muito inicial.",
        "fund_status": "ativo", "site": "https://oriacapital.com",
        "source_urls": ["https://oriacapital.com"], "verificado_em": None,
    },
    {
        "id": "investidor:positive-ventures", "name": "Positive Ventures",
        "tese": "Investe em startups que combinam retorno financeiro com impacto socioambiental positivo; inovação tecnológica voltada a grandes desafios globais.",
        "tese_themes": ["energia e transição sustentável", "saúde e ciências da vida", "agro - bioeconomia e alimentos"],
        "tese_keywords": ["impacto", "climate tech", "healthtech", "edtech", "deep-tech"],
        "setores": ["saude", "energia", "meio-ambiente", "agro"], "estagio_alvo": ["seed", "serie-a"],
        "ticket_range": None, "lead_follow": "ambos", "generalista": False,
        "anti_tese": "Não investe em negócios sem alinhamento com impacto positivo mensurável.",
        "fund_status": "ativo", "site": "https://positiveventures.com",
        "source_urls": ["https://positiveventures.com"], "verificado_em": None,
    },
    {
        "id": "investidor:antler-brasil", "name": "Antler",
        "tese": "Investe e cofunda startups desde a fase de formação da empresa, apoiando empreendedores de tecnologia em estágio inicial.",
        "tese_themes": [], "tese_keywords": ["day-zero", "pre-seed", "technology", "startup studio", "founders"],
        "setores": ["multissetorial"], "estagio_alvo": ["pre-seed", "seed"],
        "ticket_range": None, "lead_follow": "lead", "generalista": True,
        "anti_tese": "Não é focado em rodadas growth ou empresas maduras.",
        "fund_status": "ativo", "site": "https://www.antler.co",
        "source_urls": ["https://www.antler.co"], "verificado_em": None,
    },
    {
        "id": "investidor:domo-vc", "name": "DOMO.VC",
        "tese": "Investe em startups brasileiras de tecnologia com potencial de crescimento e escalabilidade; atua em diferentes verticais digitais.",
        "tese_themes": [], "tese_keywords": ["software", "saas", "digital", "b2b", "tech"],
        "setores": ["multissetorial"], "estagio_alvo": ["seed", "serie-a"],
        "ticket_range": None, "lead_follow": "ambos", "generalista": True,
        "anti_tese": "Não possui foco exclusivo em um setor industrial específico.",
        "fund_status": "ativo", "site": "https://domo.vc",
        "source_urls": ["https://domo.vc"], "verificado_em": None,
    },
]


def _validate(f: dict, themes: set[str], setores: set[str], estagios: set[str]) -> list[str]:
    errs = []
    bad_t = [t for t in f["tese_themes"] if t not in themes]
    if bad_t:
        errs.append(f"tese_themes fora do vocab: {bad_t}")
    bad_s = [s for s in f["setores"] if s not in setores]
    if bad_s:
        errs.append(f"setores fora do vocab: {bad_s}")
    bad_e = [e for e in f["estagio_alvo"] if e not in estagios]
    if bad_e:
        errs.append(f"estagio_alvo fora do vocab: {bad_e}")
    if f["generalista"] and f["tese_themes"]:
        errs.append("generalista=true mas tese_themes não vazio (invariante)")
    return errs


def main() -> None:
    themes = set(ws.tema_vocab())
    setores = set(ws.load().get("setor_vocab", []))
    estagios = set(ws.load().get("estagio_vocab", []))

    inv = kg_store.load("investidores", default={})
    existing = inv.get("investidores", [])
    existing_ids = {f["id"] for f in existing}

    added, skipped, rejected = [], [], []
    for f in NEW_FUNDS:
        if f["id"] in existing_ids:
            skipped.append(f["id"])
            continue
        errs = _validate(f, themes, setores, estagios)
        if errs:
            rejected.append((f["id"], errs))
            continue
        existing.append(f)
        added.append(f["id"])

    # Rebuild themes_index (tema → ids), total, last_updated.
    idx: dict[str, list[str]] = {}
    for f in existing:
        for t in f.get("tese_themes", []):
            idx.setdefault(t, []).append(f["id"])
    inv["investidores"] = existing
    inv["themes_index"] = idx
    inv["total_investidores"] = len(existing)
    inv["last_updated"] = _dt.date.today().isoformat()
    kg_store.save("investidores", inv)

    print(f"Adicionados ({len(added)}): {added}")
    print(f"Já existiam, pulados ({len(skipped)}): {skipped}")
    print(f"Rejeitados por validação ({len(rejected)}): {rejected}")
    print(f"Total agora: {inv['total_investidores']} fundos")


if __name__ == "__main__":
    main()
