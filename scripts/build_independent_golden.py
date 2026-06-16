#!/usr/bin/env python3
"""Monta o golden RAG de PROVENIÊNCIA INDEPENDENTE (NotebookLM).

Diferente de scripts/generate_golden.py (que gera o gold_text a partir dos
PRÓPRIOS chunks do baseline → viés baseline-friendly, near-ceiling), aqui o
gold_text é um trecho VERBATIM lido do PDF-fonte por um humano via NotebookLM.
A proveniência do gabarito é independente do pipeline → mede de fato.

Os 4 blocos abaixo foram gerados no NotebookLM (1 edital por notebook) e
revisados à mão (gold_text = recorte literal do edital). O `edital_id` de cada
bloco foi CORRIGIDO aqui: no template original todos saíram como finep:774, mas
são 4 editais distintos (a tag errada quebraria o retrieval, que é escopado por
edital_id — retriever.py WHERE edital_id = ANY).

Saída: eval_data/golden/finep_independent.json (mesmo shape de finep.json).
Roda: python scripts/build_independent_golden.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval_data" / "golden" / "finep_independent.json"

# edital_id correto -> casos (query, gold_text, category) verbatim do NotebookLM.
BLOCKS: dict[str, list[dict]] = {
    # FIP Bioeconomia e Sustentabilidade (arquivo: FIP_Bioeconomia_..._2a_rerratificacao)
    "finep:762": [
        {"query": "Qual o montante total de dinheiro que a Finep pretende colocar nessa chamada para os fundos?",
         "gold_text": "2.1. Os recursos a serem subscritos como resultado desta chamada tem o valor máximo de R$ 60.000.000 (sessenta milhões de reais).",
         "category": "financial"},
        {"query": "Qual o valor máximo que cada um dos fundos selecionados pode receber de investimento?",
         "gold_text": "2.2. Os 2 (dois) FUNDOS mais bem classificados poderão ter, inicialmente, até R$30.000.000,00 (trinta milhões de reais) de capital subscrito pelo FNDCT e/ou Finep.",
         "category": "financial"},
        {"query": "O que a Finep define tecnicamente como bioeconomia para os investimentos deste edital?",
         "gold_text": "3.2.3. A bioeconomia é entendida como o uso de recursos biológicos para produção e oferta de bens e serviços. Seguindo Convenção sobre Diversidade Biológica (“CDB”), tratado da Organização das Nações Unidas (ONU) aprovado e ratificado pelo Brasil (Decreto Legislativo nº 2/1994 e Decreto Federal nº 2.519/1998), recursos biológicos são recursos genéticos, organismos ou partes destes, populações, ou qualquer outro componente biótico de ecossistemas, de real ou potencial utilidade ou valor para a humanidade.",
         "category": "theme"},
        {"query": "Startups com sede no exterior podem receber investimento dos fundos selecionados?",
         "gold_text": "3.2.1. As SOCIEDADES-ALVO a serem investidas com recursos do FNDCT e/ou Finep devem: [...] (iii) ter sede no território nacional no momento do investimento.",
         "category": "eligibility"},
        {"query": "Qual é a data limite para o envio das propostas pelos gestores de fundos?",
         "gold_text": "2 Término do período de envio de PROPOSTAS 18/12/2025",
         "category": "deadline"},
        {"query": "A inovação é um critério obrigatório para as empresas que o fundo vai investir?",
         "gold_text": "(ii) Possuir a inovação como elemento central de sua atuação e estratégia de negócios, nos termos da Lei nº 10.973/2004 e da Lei nº 11.540/2007.",
         "category": "eligibility"},
        {"query": "Como deve ser feito o envio das propostas para esta chamada pública?",
         "gold_text": "4.1. As PROPOSTAS deverão ser enviadas exclusivamente por via eletrônica (https://forms.finep.gov.br/forms/externo/) até a data e horário limite previstos no Cronograma da Seleção Pública, segundo orientação contida neste Edital.",
         "category": "process"},
        {"query": "Quais são as notas e intervalos que a banca avaliadora pode usar na segunda etapa?",
         "gold_text": "4.5.2. Nesta Segunda Etapa, a Banca Avaliadora será composta por profissionais da Finep, os quais atribuirão notas de 1 (um) a 5 (cinco) segundo os critérios de avaliação estabelecidos neste Edital. Conforme seu julgamento, os membros da Banca Avaliadora poderão usar intervalos de 0,5 (zero vígula cinco) pontos, isto é, as notas poderão ser as seguintes: 1,00; 1,50; 2,00; 2,50; 3,00; 3,50; 4,00; 4,50 e 5,00.",
         "category": "criteria"},
        {"query": "Existem áreas ou setores de atividade onde o fundo é proibido de investir?",
         "gold_text": "3.6.6. O Regulamento deverá prever que os recursos do FNDCT e/ou da Finep não poderão ser usados para investimento em sociedades cuja atividade esteja ligada aos seguintes setores: . Tabaco e produtos fumígenos . Tecnologias de produção de fumo e dispositivos eletrônicos para fumar (DEF) . Bancário/Financeiro . Atividades de Instituições Financeiras, tais como bancos, caixas econômicas e agências de fomento",
         "category": "theme"},
        {"query": "O fundo ganha alguma vantagem se decidir investir em empresas das regiões Norte ou Nordeste?",
         "gold_text": "3.2.8. O estabelecimento de alocação mínima de recursos investidos nas Regiões Norte, Nordeste e Centro-oeste não é obrigatório na Política de Investimentos do FUNDO, porém será considerado positivamente no processo de seleção, como exposto nos itens 4.5.3 e 4.6.3 deste Edital.",
         "category": "criteria"},
    ],
    # Finep Mais Inovação Brasil – Rodada 2 – Cadeias Agroindustriais (arquivo: C_Agro_Regulamento)
    "finep:774": [
        {"query": "Sou microempreendedor individual (MEI), posso submeter meu projeto para esse edital?",
         "gold_text": "2.2. Além de outras figuras que não se enquadrem na definição do item 2.1, não são elegíveis à Subvenção Econômica em Fluxo Contínuo as pessoas jurídicas sem finalidade lucrativa (associação, fundação, cooperativa); empresário individual e microempreendedor individual.",
         "category": "eligibility"},
        {"query": "Minha empresa foi aberta este ano. Existe alguma restrição quanto à data de registro na Junta Comercial?",
         "gold_text": "a) Ter realizado o registro na Junta Comercial ou no Registro Civil das Pessoas Jurídicas (RCPJ) de sua sede até 31/12 do ano anterior ao de submissão da proposta;",
         "category": "eligibility"},
        {"query": "É obrigatório ter uma Instituição Científica e Tecnológica (ICT) como parceira no desenvolvimento do projeto?",
         "gold_text": "2.6. É obrigatória a participação de Instituições Científicas, Tecnológicas e de Inovação (ICTs) como parceiras nos projetos, devendo o cronograma de execução relacionar as atividades a serem executadas por tais instituições, com reflexo, ainda, na relação de itens do projeto, que deverá prever o pagamento correspondente na rubrica “serviços de consultoria”.",
         "category": "eligibility"},
        {"query": "Posso usar outros recursos não-reembolsáveis que recebi de fomento para compor a minha contrapartida?",
         "gold_text": "4.6. Será vedada a utilização como Contrapartida dos recursos de investimento em pesquisa e desenvolvimento decorrentes de contratos de concessão de serviços públicos, de regulações setoriais ou quaisquer outros recursos não-reembolsáveis.",
         "category": "financial"},
        {"query": "Até que dia e horário posso enviar a minha proposta pelo sistema?",
         "gold_text": "6.3. As propostas poderão ser encaminhadas até a data limite de 30/09/2026, 18h00 (horário de Brasília), ou no caso de ocorrência da hipótese prevista no item 6.5.",
         "category": "deadline"},
        {"query": "Em qual site ou plataforma devo preencher o formulário de apresentação da proposta?",
         "gold_text": "6.7. A proposta deverá ser preenchida na plataforma da Finep disponível no endereço https://financiamento.finep.gov.br/. Não serão aceitas propostas e documentação encaminhadas por qualquer outro meio que não seja a plataforma disponibilizada para apresentação das propostas, seja meio físico ou digital.",
         "category": "process"},
        {"query": "Preciso mandar algum vídeo gravado apresentando o projeto e a minha startup?",
         "gold_text": "6.9. As empresas participantes deverão enviar link de vídeo de até 10 minutos, com os seguintes conteúdos: (i) apresentação da inovação da proposta e da relevância do projeto para o atendimento dos objetivos da Seleção Pública; (ii) demonstração da capacidade técnica e a infraestrutura da empresa e apresentação de eventuais parceiros para realização do projeto.",
         "category": "process"},
        {"query": "Quais são os critérios de avaliação que a Finep vai usar para julgar o mérito do meu projeto?",
         "gold_text": "7.2.1. Os projetos habilitados na primeira etapa serão avaliados pela Finep, conforme metodologia própria, com base nos critérios de Consistência da Proposta, Grau de Inovação, Relevância da Inovação e Regionalização.",
         "category": "criteria"},
        {"query": "Ter muitos mestres e doutores na equipe executora ajuda na minha pontuação final?",
         "gold_text": "Qualificação da Equipe Avalia o nível de qualificação da equipe executora do projeto. Quanto maior a participação relativa de mestres e doutores, maior será a nota atribuída. 0, 1 e 2 1",
         "category": "criteria"},
        {"query": "Qual é a base legal e o foco temático de cadeias agroindustriais definido para esta chamada?",
         "gold_text": "1.3. Esta Chamada decorre da Resolução CNDI/MDIC Nº 1, de 6 de julho de 2023, em especial no seu artigo 4º, item I - Cadeias agroindustriais sustentáveis e digitais para a segurança alimentar e nutricional e a seus objetivos específicos previstos no Art. 6º",
         "category": "theme"},
    ],
    # FIP Nordeste Capital Semente (arquivo: Edital_FIP_Nordeste-Rerratificacao)
    "finep:743": [
        {"query": "Qual é o valor total de recursos que esse fundo pretende reunir para investir em startups?",
         "gold_text": "O Capital Comprometido Alvo do Fundo é de R$ 120.000.000,00 (cento e vinte milhões de reais). Este valor poderá ser alterado a critério dos cotistas do Fundo.",
         "category": "financial"},
        {"query": "Minha startup precisa estar em algum lugar específico para receber o aporte?",
         "gold_text": "Os investimentos do FUNDO destinar-se-ão a startups de múltiplos setores, localizadas exclusivamente na área de atuação do BNB.",
         "category": "eligibility"},
        {"query": "Qual é o limite de faturamento anual para uma empresa ser elegível ao investimento?",
         "gold_text": "O FUNDO deverá investir em empresas com faturamento de até R$ 4.800.000,00 (quatro milhões e oitocentos mil reais) no ano anterior ao investimento.",
         "category": "eligibility"},
        {"query": "O fundo pode deter o controle majoritário da minha empresa?",
         "gold_text": "O FUNDO buscará participações minoritárias influentes, amparadas em acordo de quotistas/acionistas ou instrumento jurídico semelhante. Em nenhuma hipótese, o percentual de participação do FUNDO no capital social da startup investida poderá ser igual ou superior a 50% (cinquenta por cento).",
         "category": "financial"},
        {"query": "Quais são as áreas ou setores que o edital pretende atender?",
         "gold_text": "O FUNDO será multisetorial. Como forma de diversificação e controle de riscos, o FUNDO não investirá mais do que 30% (trinta por cento) do Capital Comprometido em um único setor.",
         "category": "theme"},
        {"query": "Uma startup que tenha a maioria do capital nas mãos de estrangeiros pode ser investida?",
         "gold_text": "as startups deverão: * ser sociedades empresárias cuja maioria do capital votante seja nacional no momento da aprovação do investimento;",
         "category": "eligibility"},
        {"query": "Qual o prazo final para o envio das propostas de estruturação do fundo pelos gestores?",
         "gold_text": "Término do período de envio de PROPOSTAS 06/08/2024",
         "category": "deadline"},
        {"query": "O que exatamente este edital define como uma startup?",
         "gold_text": "Entende-se por startup a pessoa jurídica de direito privado, com sede no Brasil e constituída conforme a legislação brasileira, que tenha base inovadora, alto potencial de crescimento e retorno, e possua modelo de negócio escalável.",
         "category": "criteria"},
        {"query": "Quais são os instrumentos financeiros que o fundo utiliza para entrar nas empresas?",
         "gold_text": "comunhão de recursos destinada à aquisição de ações, bônus de subscrição, debêntures simples, outros títulos e valores mobiliários conversíveis ou permutáveis em ações de emissão de startups, bem como títulos e valores mobiliários representativos de participação em startups.",
         "category": "process"},
        {"query": "As startups que receberem o investimento terão que passar por auditoria externa?",
         "gold_text": "obrigatoriedade de auditoria anual das demonstrações contábeis por auditores independentes registrados na CVM das startups investidas pelo FUNDO, exceto quando a auditoria for dispensada por força de lei;",
         "category": "eligibility"},
    ],
    # Desafios Tecnológicos para Agricultura Familiar / DFAF (arquivo: DFAF_Edital_1a_RERRATIFICACAO)
    "finep:769": [
        {"query": "O que exatamente o governo quer que as empresas desenvolvam com esse recurso?",
         "gold_text": "2.1.1 Desenvolvimento de um de trator de pequeno porte e, no mínimo, 6 implementos agrícolas compatíveis. 2.1.2 Ao final do projeto deverão ser doados 6 pacotes tecnológico (objetos do projeto: trator + implementos financiados) para cooperativa(s)/associação(ões) de agricultores indicada(s) na proposta.",
         "category": "theme"},
        {"query": "Qual o valor máximo que posso captar se eu entregar o trator com todos os implementos opcionais listados?",
         "gold_text": "Trator + 6 implementos obrigatórios e todos os 7 implementos desejáveis. R$ 60.000.000,00",
         "category": "financial"},
        {"query": "Minha empresa foi aberta este ano, eu já posso me candidatar?",
         "gold_text": "Ter realizado o registro na Junta Comercial ou no Registro Civil das Pessoas Jurídicas (RCPJ) de sua sede até 31/12 do ano anterior ao da submissão da proposta;",
         "category": "eligibility"},
        {"query": "Quanto eu preciso colocar de dinheiro do meu próprio bolso se minha empresa for de pequeno porte?",
         "gold_text": "Microempresa e Empresa de Pequeno Porte. Inferior a R$ 4.800.000,00. 2,00%",
         "category": "financial"},
        {"query": "Até que dia e hora eu consigo enviar o projeto pelo sistema da Finep?",
         "gold_text": "3. Término do prazo para envio eletrônico da proposta (até às 18h00 - horário de Brasília). 13/03/2026",
         "category": "deadline"},
        {"query": "Qual é a pontuação mínima que eu preciso tirar para não ser cortado na análise de mérito?",
         "gold_text": "12.6 Serão consideradas aprovadas na etapa de Análise de Mérito as propostas que obtenham pontuação mínima de 1,4 pontos (um vírgula quatro pontos).",
         "category": "criteria"},
        {"query": "Por qual site eu faço a submissão oficial da minha proposta?",
         "gold_text": "11.2. A proposta deverá ser preenchida na plataforma da Finep disponível no endereço https://financiamento.finep.gov.br/.",
         "category": "process"},
        {"query": "Sou MEI, posso participar como proponente?",
         "gold_text": "4.1.2. Além de outras figuras que não se enquadrem na definição do item 4.1, não são elegíveis à Subvenção Econômica as pessoas jurídicas sem finalidade lucrativa e as pessoas físicas (associação, fundação, empresário individual, o microempreendedor individual e a Empresa Simples de Inovação).",
         "category": "eligibility"},
        {"query": "Qual o nível de maturidade da tecnologia que o projeto precisa atingir ao final?",
         "gold_text": "atividades estejam compreendidas entre os níveis de maturidade tecnológica (TRL) 3 a 8, sendo que os projetos devem necessariamente prever o atingimento do TRL 8 (sistema qualificado e finalizado), conforme conceito apresentado no Anexo 1 deste Edital, durante o prazo de execução do projeto.",
         "category": "theme"},
        {"query": "Se minha nota for igual a de outro concorrente, qual o primeiro critério para desempatar?",
         "gold_text": "12.7.1. Em caso de empate de uma ou mais propostas, o desempate observará a seguinte ordem: a) Maior nota no critério “Implementos”;",
         "category": "criteria"},
    ],
}


def build() -> dict:
    queries = []
    for edital_id, cases in BLOCKS.items():
        for n, c in enumerate(cases, start=1):
            queries.append({
                "id": f"{edital_id.replace(':', '_')}_q{n}",
                "edital_id": edital_id,
                "query": c["query"],
                "expected": [],  # opcional: token-recall usa só gold_text
                "gold_text": c["gold_text"],
                "category": c["category"],
            })
    return {
        "source": "finep",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "notebooklm-independent",
        "queries": queries,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    by = {}
    for q in data["queries"]:
        by[q["edital_id"]] = by.get(q["edital_id"], 0) + 1
    print(f"Escrito {OUT} — {len(data['queries'])} queries")
    for k, v in by.items():
        print(f"  {k}: {v}")
