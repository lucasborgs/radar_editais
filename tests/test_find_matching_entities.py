"""Fase B do Hipergrado Sprint 3 — match de entidades (investidor/programa/ICT).

Testa a ATRIBUIÇÃO via arestas nativas (`_entity_attribution`) — a lógica nova que
faz o match LER as hiperarestas pela 1ª vez (obj 1): um nó de conteúdo que casou é
subido até a entidade dona pela aresta nativa (`abrange_tema`/`financia`/...). Pura,
sem disco nem embeddings.
"""
from __future__ import annotations

from core.services.hypergraph_match import (
    CATALOG_FILES,
    ENTITY_TYPES,
    _entity_attribution,
)

# Catálogo sintético cobrindo os dois caminhos:
#  • ICT casa por TEMA (descrição pobre, sinal nos temas via `abrange_tema`)
#  • Programa casa DIRETO (sem tema no arquivo, descrição rica)
GRAPHS = {
    "ict": {
        "nodes": [
            {"name": "SENAI/SP", "type": "ICT", "description": "Instituto Privado"},
            {"name": "Bioindústria", "type": "Tema"},
            {"name": "Visão Computacional", "type": "Tecnologia"},
        ],
        "edges": [
            {
                "type": "abrange_tema",
                "members": ["senai/sp", "bioindústria", "visão computacional"],
                "description": "áreas de atuação",
            },
        ],
    },
    "investidores": {
        "nodes": [
            {"name": "The Yield Lab", "type": "Investidor", "description": "VC agro"},
            {"name": "agro - bioeconomia", "type": "Tema"},
        ],
        "edges": [
            {
                "type": "financia",
                "members": ["the yield lab", "agro - bioeconomia"],
                "description": "tese",
            },
        ],
    },
    "programas": {
        "nodes": [
            {"name": "Centelha", "type": "Programa", "description": "estímulo a startups"},
        ],
        "edges": [],
    },
}


def test_constants():
    assert ENTITY_TYPES == {"Investidor", "Programa", "ICT"}
    assert CATALOG_FILES == {"investidores", "programas", "ict"}


def test_attribution_ict_theme_to_owner():
    attribution, _ = _entity_attribution(GRAPHS)
    # tema "bioindústria" sobe até a ICT dona via abrange_tema
    assert ("ICT", "SENAI/SP") in attribution["ict"]["bioindústria"]
    assert ("ICT", "SENAI/SP") in attribution["ict"]["visão computacional"]


def test_attribution_investidor_financia():
    attribution, _ = _entity_attribution(GRAPHS)
    assert ("Investidor", "The Yield Lab") in attribution["investidores"]["agro - bioeconomia"]


def test_entity_index_carries_description():
    _, entity_index = _entity_attribution(GRAPHS)
    # caminho direto: o Programa não tem tema; a descrição vem do índice de entidades
    assert entity_index["programas"]["centelha"] == ("Programa", "Centelha", "estímulo a startups")


def test_attribution_ignores_unknown_files():
    attribution, _ = _entity_attribution({"finep__589": {"nodes": [], "edges": []}})
    # só varre os arquivos de catálogo
    assert attribution == {}
