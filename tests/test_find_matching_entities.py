"""Fase B do Hipergrado Sprint 3 — match de entidades (investidor/programa/ICT).

Testa a ATRIBUIÇÃO via arestas nativas (`_entity_attribution`) — a lógica nova que
faz o match LER as hiperarestas pela 1ª vez (obj 1): um nó de conteúdo que casou é
subido até a entidade dona pela aresta nativa (`abrange_tema`/`financia`/...). Pura,
sem disco nem embeddings.
"""
from __future__ import annotations

from core.kg.migrate_v2 import migrate_to_v2
from core.services.hypergraph_match import (
    CATALOG_FILES,
    ENTITY_KINDS,
    _entity_attribution,
)

# Catálogo sintético cobrindo os dois caminhos (fixtures v1 — o teste as eleva a v2
# via migrate_to_v2, como o kg_store faz na leitura):
#  • ICT casa por TEMA (descrição pobre, sinal nos temas via `abrange_tema`)
#  • Programa casa DIRETO (sem tema no arquivo, descrição rica)
_GRAPHS_V1 = {
    "ict": {
        "nodes": [
            {"name": "CERTI", "type": "ICT", "description": "Instituto Privado"},
            {"name": "Bioindústria", "type": "Tema"},
            {"name": "Visão Computacional", "type": "Tecnologia"},
        ],
        "edges": [
            {
                "type": "abrange_tema",
                "members": ["certi", "bioindústria", "visão computacional"],
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
GRAPHS = {fk: migrate_to_v2(g) for fk, g in _GRAPHS_V1.items()}


def test_constants():
    assert ENTITY_KINDS == {"investidor", "programa", "ict"}
    assert CATALOG_FILES == {"investidores", "programas", "ict"}


def test_attribution_ict_theme_to_owner():
    attribution, _ = _entity_attribution(GRAPHS)
    # tema "bioindústria" sobe até a ICT (Ator/ict) dona via abrange_tema
    assert ("ict", "CERTI") in attribution["ict"]["bioindústria"]
    assert ("ict", "CERTI") in attribution["ict"]["visão computacional"]


def test_attribution_investidor_financia():
    attribution, _ = _entity_attribution(GRAPHS)
    assert ("investidor", "The Yield Lab") in attribution["investidores"]["agro - bioeconomia"]


def test_entity_index_carries_description():
    _, entity_index = _entity_attribution(GRAPHS)
    # caminho direto: o Programa (Oportunidade/programa) não tem tema; a descrição
    # vem do índice de entidades
    assert entity_index["programas"]["centelha"] == ("programa", "Centelha", "estímulo a startups")


def test_attribution_ignores_unknown_files():
    attribution, _ = _entity_attribution({"finep__589": {"nodes": [], "edges": []}})
    # só varre os arquivos de catálogo
    assert attribution == {}
