"""Caminhos de inovação por domínio (spec product-pathways-domain-matching.md).

Cobre: classificação de tipo, contrato mínimo do caminho, explicação por
domínio (confirmados/inferidos/pendentes/lacunas), jornada intenção-sem-projeto
e a garantia de que investidores nunca viram caminho.
"""
from radar.core.services import domain_paths


def _entity(**over) -> dict:
    base = {
        "kind": "edital",
        "native_id": "finep:1",
        "name": "Edital X",
        "description": "Financiamento de projetos de P&D",
        "status": "aberta",
        "setores": ["Saúde"],
        "metadata": {"url": "https://ex.com"},
        "requisitos_texto": [],
        "constraints": [],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Classificação de tipo
# ---------------------------------------------------------------------------

def test_classify_edital_default_financiamento():
    assert domain_paths.classify_tipo(_entity()) == domain_paths.PATH_TIPO_FINANCIAMENTO


def test_classify_credito_por_texto():
    e = _entity(description="Financiamento reembolsável — linha de crédito para inovação")
    assert domain_paths.classify_tipo(e) == domain_paths.PATH_TIPO_CREDITO


def test_classify_nao_reembolsavel_eh_subvencao():
    """'Não reembolsável' (subvenção) não vira crédito — vira Subvenção."""
    e = _entity(description="Financiamento não reembolsável (subvenção econômica)")
    assert domain_paths.classify_tipo(e) == domain_paths.PATH_TIPO_SUBVENCAO


def test_classify_subvencao_por_tipo_curated():
    """O tipo curado em metadata.tipo refina subvenção mesmo com descrição genérica."""
    e = _entity(description="Chamada pública de apoio", metadata={"tipo": "Subvenção Econômica"})
    assert domain_paths.classify_tipo(e) == domain_paths.PATH_TIPO_SUBVENCAO


def test_classify_bolsa_por_texto_e_por_tipo():
    e_texto = _entity(description="Concessão de bolsa de pesquisa para empresas")
    e_tipo = _entity(description="Chamada de apoio", metadata={"tipo": "bolsa"})
    assert domain_paths.classify_tipo(e_texto) == domain_paths.PATH_TIPO_BOLSA
    assert domain_paths.classify_tipo(e_tipo) == domain_paths.PATH_TIPO_BOLSA


def test_classify_desafio_por_texto():
    e = _entity(description="Desafio de inovação aberta com premiação em dinheiro")
    assert domain_paths.classify_tipo(e) == domain_paths.PATH_TIPO_DESAFIO


def test_classify_programa_aceleradora_e_incubadora():
    acel = _entity(kind="programa", metadata={"tipo": "aceleracao"})
    inc = _entity(kind="programa", metadata={"tipo": "incubacao"})
    assert domain_paths.classify_tipo(acel) == domain_paths.PATH_TIPO_ACELERADORA
    assert domain_paths.classify_tipo(inc) == domain_paths.PATH_TIPO_INCUBADORA


def test_classify_programa_subvencao_bolsa_e_fundo():
    sub = _entity(kind="programa", metadata={"tipo": "subvencao"})
    bolsa = _entity(kind="programa", metadata={"tipo": "bolsa"})
    fundo = _entity(kind="programa", metadata={"tipo": "fundo"})
    sem = _entity(kind="programa", metadata={})
    assert domain_paths.classify_tipo(sub) == domain_paths.PATH_TIPO_SUBVENCAO
    assert domain_paths.classify_tipo(bolsa) == domain_paths.PATH_TIPO_BOLSA
    assert domain_paths.classify_tipo(fundo) == domain_paths.PATH_TIPO_FINANCIAMENTO
    assert domain_paths.classify_tipo(sem) == domain_paths.PATH_TIPO_FINANCIAMENTO


def test_classify_ict_e_investidor():
    assert domain_paths.classify_tipo(_entity(kind="ict")) == domain_paths.PATH_TIPO_ICT
    assert domain_paths.classify_tipo(_entity(kind="investidor")) is None


# ---------------------------------------------------------------------------
# Projeto definido vs intenção
# ---------------------------------------------------------------------------

def test_has_project():
    assert domain_paths.has_project({"one_liner": "fazemos X"})
    assert domain_paths.has_project({"portfolio_projetos": "projeto A"})
    assert not domain_paths.has_project({"descricao_atividades": "atividades da empresa"})
    assert not domain_paths.has_project(None)


# ---------------------------------------------------------------------------
# Contrato mínimo do caminho
# ---------------------------------------------------------------------------

def test_build_path_contrato_minimo():
    path = domain_paths.build_path(
        _entity(), profile={"nome": "ACME", "one_liner": "diagnóstico de saúde"},
        eleg=None, url="https://ex.com",
    )
    assert path is not None
    assert set(path) == {
        "tipo", "entidade", "objetivo", "requisitos",
        "canal_de_acesso", "evidencias", "status", "proximo_passo",
    }
    assert path["tipo"] == domain_paths.PATH_TIPO_FINANCIAMENTO
    assert path["entidade"] == "finep:1"
    assert path["canal_de_acesso"] == "https://ex.com"
    assert path["status"] == "possibilidade"  # sem eleg avaliada
    assert path["proximo_passo"]


def test_build_path_investidor_none():
    assert domain_paths.build_path(
        _entity(kind="investidor"), profile=None, eleg=None,
    ) is None


def test_build_path_evidencias_de_temas():
    path = domain_paths.build_path(
        _entity(setores=["Saúde", "IoT"]), profile={"nome": "ACME"},
        eleg=None, shared_themes={"Saúde"},
    )
    temas = [ev for ev in path["evidencias"] if ev["tipo"] == "tema"]
    assert temas and "Saúde" in temas[0]["detalhe"]


# ---------------------------------------------------------------------------
# Explicação por domínio
# ---------------------------------------------------------------------------

def test_explicacao_separa_confirmados_inferidos_pendentes():
    eleg = {"status": "nao_verificada", "unsat": [], "unknown": ["porte não informado"]}
    expl = domain_paths.build_explanation(
        domain_paths.PATH_TIPO_FINANCIAMENTO, e=_entity(setores=["Saúde"]),
        eleg=eleg, profile={"nome": "ACME"}, has_project=True, shared_themes={"Saúde"},
    )
    assert expl["confirmados"] and "Saúde" in expl["confirmados"][0]
    assert any("não é promessa de aprovação" in i for i in expl["inferidos"])
    assert "porte não informado" in expl["pendentes"]


def test_explicacao_elegivel_confirma_constraints():
    eleg = {"status": "elegivel", "unsat": [], "unknown": []}
    expl = domain_paths.build_explanation(
        domain_paths.PATH_TIPO_FINANCIAMENTO, e=_entity(),
        eleg=eleg, profile={"nome": "ACME"}, has_project=True, shared_themes=set(),
    )
    assert any("satisfeitos" in c for c in expl["confirmados"])


def test_explicacao_intencao_sem_projeto_nao_declara_elegibilidade():
    """Aceite 3: intenção sem projeto vira possibilidade revisável, sem claim."""
    expl = domain_paths.build_explanation(
        domain_paths.PATH_TIPO_FINANCIAMENTO, e=_entity(), eleg=None,
        profile={"descricao_atividades": "fazemos consultoria"}, has_project=False,
        shared_themes=set(),
    )
    assert any("Defina o projeto/hipótese" in p for p in expl["pendentes"])
    assert any("intenção" in i for i in expl["inferidos"])
    # fatos factuais (status da oferta) podem aparecer; claim de elegibilidade não
    assert not any("Constraints de elegibilidade" in c for c in expl["confirmados"])


def test_dois_tipos_mesma_intencao_criterios_distintos():
    """Aceite 1: dois caminhos de tipos diferentes com explicações distintas."""
    e_cred = _entity(description="Financiamento reembolsável para inovação")
    e_des = _entity(description="Desafio de inovação aberta")
    prof = {"descricao_atividades": "fazemos software", "one_liner": ""}
    expl_cred = domain_paths.build_explanation(
        domain_paths.classify_tipo(e_cred), e=e_cred, eleg=None,
        profile=prof, has_project=False,
    )
    expl_des = domain_paths.build_explanation(
        domain_paths.classify_tipo(e_des), e=e_des, eleg=None,
        profile=prof, has_project=False,
    )
    assert expl_cred["tipo"] == domain_paths.PATH_TIPO_CREDITO
    assert expl_des["tipo"] == domain_paths.PATH_TIPO_DESAFIO
    assert expl_cred["criterios"] != expl_des["criterios"]
    assert expl_cred["proximo_passo"] != expl_des["proximo_passo"]


def test_credito_subvencao_bolsa_labels_e_proximos_passos_distintos():
    """Três mecanismos de financiamento com labels e próximos passos próprios."""
    cred = _entity(description="Linha de crédito reembolsável para inovação")
    sub = _entity(description="Subvenção econômica para projetos de P&D")
    bol = _entity(description="Bolsa de pesquisa para empresas inovadoras")
    labels = {
        domain_paths.classify_tipo(cred): domain_paths.TIPO_LABEL[domain_paths.classify_tipo(cred)],
        domain_paths.classify_tipo(sub): domain_paths.TIPO_LABEL[domain_paths.classify_tipo(sub)],
        domain_paths.classify_tipo(bol): domain_paths.TIPO_LABEL[domain_paths.classify_tipo(bol)],
    }
    assert labels[domain_paths.PATH_TIPO_CREDITO] == "Crédito"
    assert labels[domain_paths.PATH_TIPO_SUBVENCAO] == "Subvenção"
    assert labels[domain_paths.PATH_TIPO_BOLSA] == "Bolsa"
    assert len(set(labels.values())) == 3  # rótulos distintos
    steps = {
        domain_paths.classify_tipo(e): domain_paths.build_path(
            e, profile={"one_liner": "projeto X"}, eleg=None,
        )["proximo_passo"]
        for e in (cred, sub, bol)
    }
    assert steps[domain_paths.PATH_TIPO_CREDITO] != steps[domain_paths.PATH_TIPO_SUBVENCAO]
    assert steps[domain_paths.PATH_TIPO_SUBVENCAO] != steps[domain_paths.PATH_TIPO_BOLSA]


def test_explicacao_ict_sem_claim_de_elegibilidade():
    expl = domain_paths.build_explanation(
        domain_paths.PATH_TIPO_ICT, e=_entity(kind="ict", setores=["Saúde"]),
        eleg=None, profile={"one_liner": "projeto X"}, has_project=True,
        shared_themes={"Saúde"},
    )
    assert any("competência" in i.lower() for i in expl["inferidos"])
    assert any("Temas em comum" in c for c in expl["confirmados"])  # fato factual, não elegibilidade
    assert not any("Constraints de elegibilidade" in c for c in expl["confirmados"])
