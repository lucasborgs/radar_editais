#!/usr/bin/env python3
"""Ingestão da curadoria externa (Parte B2) → programas.json (node-type novo).

Reconciliação: os 6 comuns (Gemini+ChatGPT) usam a versão Gemini (mais detalhada:
ticket_range + faq_url); + PIPE-FAPESP (só Gemini); + 3 únicos do ChatGPT
(startup-outreach, finep-startup, tecnova). Valida tipo/formato/vocab. Constrói
{programas, total_programas, themes_index, last_updated} (espelha investidores.json).
verificado_em=None: curadoria-LLM, ainda não verificada por humano.
"""
from __future__ import annotations

import datetime as _dt

from core.kg import kg_store
from core.kg import schema as ws

_TIPOS = {"aceleracao", "incubacao", "subvencao", "fundo", "capacitacao"}
_FORMATOS = {"cohort", "edital-periodico", "fluxo-continuo"}

PROGRAMAS = [
    {"id": "programa:centelha", "name": "Programa Centelha", "operador": "MCTI / FINEP / FAPs estaduais",
     "tipo": "subvencao", "descricao": "Programa nacional de estímulo à criação de empreendimentos inovadores: transforma ideias em negócios com capacitação e subvenção. Editais estadualizados pelas FAPs.",
     "formato": "edital-periodico", "cadencia": "anual",
     "beneficio": "Subvenção econômica (capital não-reembolsável), bolsas, mentorias e capacitação.",
     "ticket_range": {"min_brl": 50000, "max_brl": 130000}, "tese_themes": [], "setores": [], "estagio_alvo": ["pre-seed"],
     "elegibilidade": "Pessoas físicas e empresas com faturamento até R$ 4,8 mi, criadas em até 12 meses do edital estadual.",
     "site": "https://programacentelha.com.br", "faq_url": "https://programacentelha.com.br/duvidas-frequentes/",
     "source_urls": ["https://programacentelha.com.br/o-programa/"], "status": "ativo", "verificado_em": None},

    {"id": "programa:sebrae-startups", "name": "Sebrae Startups", "operador": "Sebrae",
     "tipo": "capacitacao", "descricao": "Hub do Sebrae para o ecossistema de startups: desafios regionais/setoriais, capacitação, conexão com corporações e suporte técnico.",
     "formato": "fluxo-continuo", "cadencia": "contínuo",
     "beneficio": "Mentorias, trilhas, conexão com mercado, infraestrutura tecnológica e Sebraetec.",
     "ticket_range": None, "tese_themes": [], "setores": [], "estagio_alvo": ["pre-seed", "seed"],
     "elegibilidade": "Startups formalizadas (MEI/ME/EPP) com faturamento até R$ 4,8 mi, foco em inovação e escala.",
     "site": "https://www.sebraestartups.com.br", "faq_url": "https://www.sebraestartups.com.br/faq",
     "source_urls": ["https://www.sebraestartups.com.br/sobre-nos"], "status": "ativo", "verificado_em": None},

    {"id": "programa:bndes-garagem", "name": "BNDES Garagem", "operador": "BNDES",
     "tipo": "aceleracao", "descricao": "Aceleração de startups de impacto (social/ambiental) em trilhas de Criação e Tração, equity-free.",
     "formato": "cohort", "cadencia": "anual",
     "beneficio": "Aceleração equity-free, mentorias, suporte jurídico/contábil, conexão com ecossistema de impacto.",
     "ticket_range": None, "tese_themes": [], "setores": [], "estagio_alvo": ["pre-seed", "seed"],
     "elegibilidade": "Startups de impacto socioambiental com CNPJ ativo, nas fases de criação (validação) ou tração.",
     "site": "https://garagem.bndes.gov.br", "faq_url": "https://garagem.bndes.gov.br/tire-suas-duvidas/",
     "source_urls": ["https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/inovacao/bndes-garagem"], "status": "ativo", "verificado_em": None},

    {"id": "programa:bndes-funtec", "name": "BNDES FUNTEC", "operador": "BNDES",
     "tipo": "subvencao", "descricao": "Fundo Tecnológico: apoio não-reembolsável a pesquisa aplicada, desenvolvimento experimental e inovação, em parceria ICT↔empresa.",
     "formato": "fluxo-continuo", "cadencia": "contínuo",
     "beneficio": "Apoio financeiro não-reembolsável à ICT, com contrapartida e empresa interveniente.",
     "ticket_range": None, "tese_themes": [], "setores": [], "estagio_alvo": ["seed", "serie-a", "growth"],
     "elegibilidade": "ICTs em parceria obrigatória com empresas com capacidade de industrializar/comercializar o resultado.",
     "site": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/funtec",
     "faq_url": "https://www.bndes.gov.br/wps/portal/site/home/quem-somos/canais-atendimento/perguntas-frequentes/funtec",
     "source_urls": ["https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/funtec"], "status": "ativo", "verificado_em": None},

    {"id": "programa:inovativa-brasil", "name": "InovAtiva Brasil", "operador": "MDIC / Sebrae",
     "tipo": "aceleracao", "descricao": "Maior programa de aceleração gratuito (equity-free) da América Latina: capacitação, mentoria e conexão com mercado e investidores.",
     "formato": "cohort", "cadencia": "2x/ano",
     "beneficio": "Capacitação online, mentorias equity-free, treino de pitch e Demoday para investidores.",
     "ticket_range": None, "tese_themes": [], "setores": [], "estagio_alvo": ["pre-seed", "seed"],
     "elegibilidade": "Startups de todos os setores/regiões em ideação, operação ou tração.",
     "site": "https://www.inovativabrasil.com.br", "faq_url": "https://www.inovativabrasil.com.br/faq/",
     "source_urls": ["https://www.inovativabrasil.com.br/sobre-nos/"], "status": "ativo", "verificado_em": None},

    {"id": "programa:catalisa-ict", "name": "Catalisa ICT", "operador": "Sebrae",
     "tipo": "aceleracao", "descricao": "Acelera a transferência de tecnologia da academia para o mercado: capacitação, bolsas e apoio à abertura de empresas deep-tech.",
     "formato": "edital-periodico", "cadencia": "anual",
     "beneficio": "Bolsas de pesquisa, mentorias, capacitação em gestão da inovação e conexão com subvenção/fundos.",
     "ticket_range": {"min_brl": 50000, "max_brl": 150000}, "tese_themes": [], "setores": [], "estagio_alvo": ["pre-seed"],
     "elegibilidade": "Pesquisadores vinculados a ICTs com pesquisas de alto potencial mercadológico.",
     "site": "https://sebrae.com.br/sites/PortalSebrae/catalisaict", "faq_url": None,
     "source_urls": ["https://sebrae.com.br/sites/PortalSebrae/catalisaict"], "status": "ativo", "verificado_em": None},

    {"id": "programa:pipe-fapesp", "name": "PIPE (Pesquisa Inovativa em Pequenas Empresas)", "operador": "FAPESP",
     "tipo": "subvencao", "descricao": "Um dos principais programas deep-tech do país: pesquisa tecnológica em pequenas empresas, em fases de prova de conceito, desenvolvimento e comercialização.",
     "formato": "fluxo-continuo", "cadencia": "contínuo",
     "beneficio": "Subvenção econômica de alto valor para pesquisa, equipamentos e bolsas.",
     "ticket_range": {"min_brl": 300000, "max_brl": 1500000}, "tese_themes": [], "setores": [], "estagio_alvo": ["pre-seed", "seed", "serie-a"],
     "elegibilidade": "Micro e pequenas empresas de base tecnológica (até 250 funcionários) sediadas em SP.",
     "site": "https://fapesp.br/pipe", "faq_url": "https://fapesp.br/pipe/perguntasfrequentes",
     "source_urls": ["https://fapesp.br/pipe/sobre"], "status": "ativo", "verificado_em": None},

    {"id": "programa:startup-outreach-brasil", "name": "Startup OutReach Brasil", "operador": "ApexBrasil / Sebrae",
     "tipo": "capacitacao", "descricao": "Programa recorrente de internacionalização de startups brasileiras: preparação para expansão e acesso a mercados/investidores externos.",
     "formato": "cohort", "cadencia": "anual",
     "beneficio": "Capacitação para internacionalização, mentorias e conexões internacionais.",
     "ticket_range": None, "tese_themes": [], "setores": [], "estagio_alvo": ["seed", "serie-a"],
     "elegibilidade": "Startups brasileiras com produto validado e interesse em expansão internacional.",
     "site": "https://www.apexbrasil.com.br", "faq_url": None,
     "source_urls": ["https://www.apexbrasil.com.br"], "status": "ativo", "verificado_em": None},

    {"id": "programa:finep-startup", "name": "Finep Startup", "operador": "Finep",
     "tipo": "subvencao", "descricao": "Investimento da Finep em startups tecnológicas de alto potencial via chamadas periódicas para empresas inovadoras em estágio inicial.",
     "formato": "edital-periodico", "cadencia": "anual",
     "beneficio": "Aporte de capital para crescimento e desenvolvimento tecnológico.",
     "ticket_range": None, "tese_themes": [], "setores": [], "estagio_alvo": ["seed"],
     "elegibilidade": "Startups inovadoras com CNPJ e tecnologia própria, conforme critérios da chamada.",
     "site": "https://www.finep.gov.br", "faq_url": None,
     "source_urls": ["https://www.finep.gov.br"], "status": "ativo", "verificado_em": None},

    {"id": "programa:tecnova", "name": "TECNOVA", "operador": "Finep / FAPs estaduais",
     "tipo": "subvencao", "descricao": "Subvenção econômica para micro e pequenas empresas inovadoras, executada com FAPs estaduais; múltiplas edições com foco em inovação empresarial.",
     "formato": "edital-periodico", "cadencia": "anual",
     "beneficio": "Subvenção econômica não-reembolsável para inovação.",
     "ticket_range": None, "tese_themes": [], "setores": [], "estagio_alvo": ["seed"],
     "elegibilidade": "Micro e pequenas empresas inovadoras, conforme regras estaduais.",
     "site": "https://www.finep.gov.br", "faq_url": None,
     "source_urls": ["https://www.finep.gov.br"], "status": "ativo", "verificado_em": None},
]


def _validate(p: dict, themes: set[str], setores: set[str], estagios: set[str]) -> list[str]:
    errs = []
    if p["tipo"] not in _TIPOS:
        errs.append(f"tipo inválido: {p['tipo']}")
    if p["formato"] not in _FORMATOS:
        errs.append(f"formato inválido: {p['formato']}")
    if [t for t in p["tese_themes"] if t not in themes]:
        errs.append(f"tese_themes fora do vocab: {[t for t in p['tese_themes'] if t not in themes]}")
    if [s for s in p["setores"] if s not in setores]:
        errs.append(f"setores fora do vocab: {[s for s in p['setores'] if s not in setores]}")
    if [e for e in p["estagio_alvo"] if e not in estagios]:
        errs.append(f"estagio_alvo fora do vocab: {[e for e in p['estagio_alvo'] if e not in estagios]}")
    return errs


def main() -> None:
    themes = set(ws.tema_vocab())
    setores = set(ws.setor_vocab())
    estagios = set(ws.estagio_vocab())

    accepted, rejected = [], []
    for p in PROGRAMAS:
        errs = _validate(p, themes, setores, estagios)
        (rejected.append((p["id"], errs)) if errs else accepted.append(p))

    idx: dict[str, list[str]] = {}
    for p in accepted:
        for t in p.get("tese_themes", []):
            idx.setdefault(t, []).append(p["id"])

    artifact = {
        "programas": accepted,
        "total_programas": len(accepted),
        "themes_index": idx,
        "last_updated": _dt.date.today().isoformat(),
    }
    kg_store.save("programas", artifact)
    print(f"Ingeridos: {len(accepted)} programas")
    print(f"Rejeitados ({len(rejected)}): {rejected}")
    from collections import Counter
    print("Por tipo:", dict(Counter(p["tipo"] for p in accepted)))


if __name__ == "__main__":
    main()
