"""Fixtures sintéticas para SourceBundle (RT04-T01).

Três cenários obrigatórios:
1. Web: portal corporativo (program_page) + desafio específico (opportunity_page)
2. FAPESC: edital-base (base_notice) + retificação (amendment via amends_content_hash)
3. Ator ICT: material insuficiente (partial, sem descrição fabricada)
"""

from __future__ import annotations

from radar.domain.source_bundle import compute_content_hash


def web_portal_challenge() -> dict:
    """Portal corporativo Tupy sintético + desafio IA em Saúde.

    program_page é contextual; opportunity_page é active (específica).
    ID segue padrão web:<url_hash_sintético>.
    Ambas as páginas são sintéticas — não copiam nem acessam páginas reais.
    """
    program_units = [
        "O Programa Tupy de Inovação Aberta conecta startups e empresas "
        "de tecnologia a desafios reais de negócio da indústria.",
        "Podem participar empresas com CNPJ ativo há pelo menos 12 meses "
        "e solução com TRL mínimo 4.",
        "Benefícios: aporte financeiro de até R$ 200 mil, mentoria técnica "
        "e acesso à rede de parceiros.",
        "Cada desafio possui edital específico com regras, cronograma e "
        "critérios próprios.",
    ]
    challenge_units = [
        "Desafio IA em Saúde: desenvolver solução baseada em inteligência "
        "artificial para apoio ao diagnóstico de doenças raras.",
        "Premiação: R$ 150 mil em subvenção econômica + 6 meses de aceleração.",
        "Inscrições até 30/09/2026. Resultado esperado: MVP funcional "
        "validado em ambiente controlado.",
        "Critérios de avaliação: aderência ao problema, viabilidade técnica, "
        "impacto potencial e maturidade da equipe.",
    ]
    return {
        "schema_version": 1,
        "subject_kind": "opportunity",
        "subject_id": "web:a1b2c3d4e5",  # url_hash sintético
        "source": "web",
        "collected_at": "2026-07-27T10:00:00Z",
        "producer_version": "web-adapter-v1",
        "acquisition_status": "complete",
        "documents": [
            {
                "doc_name": "programa_inovacao_aberta.html",
                "units": program_units,
                "role": "program_page",
                "source_url": "https://tupy.example.com/programa-inovacao-aberta",
                "published_at": "2026-01-15",
                "content_hash": compute_content_hash(program_units),
                "authority_state": "contextual",
                "composition_order": 0,
            },
            {
                "doc_name": "desafio_ia_saude_2026.html",
                "units": challenge_units,
                "role": "opportunity_page",
                "source_url": "https://tupy.example.com/desafio-ia-saude-2026",
                "published_at": "2026-07-01",
                "content_hash": compute_content_hash(challenge_units),
                "authority_state": "active",
                "composition_order": 1,
            },
        ],
    }


def fapesc_base_amendment() -> dict:
    """Edital-base FAPESC + retificação oficial vinculada por amends_content_hash.

    base_notice é o documento original; amendment altera prazo e público.
    A retificação referencia o content_hash do edital-base via
    amends_content_hash, sem significar supersessão integral.
    """
    base_units = [
        "CHAMADA PÚBLICA FAPESC Nº 37/2026 - Subvenção Econômica à Inovação",
        "1. DO OBJETO: A presente chamada tem por objetivo selecionar "
        "projetos de inovação.",
        "2. DOS REQUISITOS: Micro e pequenas empresas com sede em Santa Catarina.",
        "3. DO CRONOGRAMA: Inscrições até 31/08/2026.",
    ]
    base_content_hash = compute_content_hash(base_units)
    amendment_units = [
        "RETIFICAÇÃO Nº 01/2026 À CHAMADA PÚBLICA FAPESC Nº 37/2026",
        "Onde se lê: 'Inscrições até 31/08/2026', "
        "leia-se: 'Inscrições até 30/09/2026'.",
        "Onde se lê: 'Micro e pequenas empresas', "
        "leia-se: 'Micro, pequenas e médias empresas'.",
        "Permanecem inalteradas as demais condições da chamada pública.",
    ]
    return {
        "schema_version": 1,
        "subject_kind": "opportunity",
        "subject_id": "fapesc:37-2026",
        "source": "fapesc",
        "collected_at": "2026-07-27T12:00:00Z",
        "producer_version": "fapesc-adapter-v1",
        "acquisition_status": "complete",
        "documents": [
            {
                "doc_name": "Edital_37_2026.pdf",
                "units": base_units,
                "role": "base_notice",
                "source_url": "https://fapesc.sc.gov.br/chamadas/37-2026",
                "published_at": "2026-06-01",
                "content_hash": base_content_hash,
                "authority_state": "active",
                "composition_order": 0,
            },
            {
                "doc_name": "Retificacao_01_37_2026.pdf",
                "units": amendment_units,
                "role": "amendment",
                "source_url": "https://fapesc.sc.gov.br/chamadas/37-2026-ret1",
                "published_at": "2026-06-15",
                "content_hash": compute_content_hash(amendment_units),
                "authority_state": "active",
                "composition_order": 1,
                "amends_content_hash": base_content_hash,
            },
        ],
    }


def actor_insufficient() -> dict:
    """Ator (ICT) com material insuficiente.

    ID canônico ict:<source>:<slug>.
    Apenas uma página institucional sem conteúdo substancial.
    acquisition_status=partial; nenhuma descrição, evidência ou
    conteúdo sintético é fabricado.
    """
    single_unit = [
        "Laboratório de Inovação Exemplo - fundado em 2020, "
        "foco em pesquisa aplicada.",
    ]
    return {
        "schema_version": 1,
        "subject_kind": "ict",
        "subject_id": "ict:exemplo:lab-inovacao",
        "source": "exemplo",
        "collected_at": "2026-07-27T14:00:00Z",
        "producer_version": "actor-catalog-v1",
        "acquisition_status": "partial",
        "documents": [
            {
                "doc_name": "pagina_institucional.html",
                "units": single_unit,
                "role": "official_page",
                "source_url": "https://exemplo.example.com/lab",
                "published_at": "2026-05-01",
                "content_hash": compute_content_hash(single_unit),
                "authority_state": "active",
                "composition_order": 0,
            },
        ],
    }
