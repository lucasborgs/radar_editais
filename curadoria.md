#Prompt A

##Gemini
[
  {
    "nome": "FAPEMIG",
    "uf": "MG",
    "site_oficial": "https://fapemig.br",
    "url_listagem_editais": "https://fapemig.br/oportunidades/chamadas-e-editais",
    "renderizacao": "spa-js",
    "estrutura_listagem": "Usa cards gerados no cliente. O título do edital age como header e há um botão 'Saiba Mais'. A lista traz de forma limpa campos de metadados explícitos: 'Lançamento', 'Submissão até', 'Resultado (Previsto)' e 'Valor (R$)'. A paginação ocorre dinamicamente por botão 'Carregar Mais' (paginação via API).",
    "pagina_individual": "Sim, cada edital ganha uma URL dedicada bem estruturada (ex: /oportunidades/chamadas-e-editais/chamada-fapemig-01-2026-programa-santos-dumont) com texto em HTML, cronogramas e anexos em PDF.",
    "formato_data": "dd de [mês abreviado]. de aaaa (ex: '03 de jun. de 2026')",
    "indicador_status": "Excelente. É distinguível visualmente nos cards pelas etiquetas ('Aberta', 'Em análise', 'Encerrada') e também existem filtros ativos na página.",
    "tem_faq": "Sim. URL: https://fapemig.br/central-de-ajuda/perguntas-frequentes",
    "foco_deeptech": "Sim. Possui linhas de inovação forte focadas em ICTs e Empresas. Exemplos recentes: 'Programa Centelha 3', 'CHAMADA FAPEMIG-SEDE 03/2026 Novo SEED: Aceleração de Startups' e 'Minas pelo Clima'.",
    "gotchas": "Conteúdo 100% carregado via chamadas assíncronas (React/Vue). Uma requisição HTTP tradicional (`curl` ou Python `requests`) trará apenas o esqueleto do site. Seu scraper precisará usar Puppeteer/Playwright ou inspecionar o Network do navegador para consumir o endpoint da API JSON diretamente."
  },
  {
    "nome": "FAPERJ",
    "uf": "RJ",
    "site_oficial": "https://www.faperj.br",
    "url_listagem_editais": "https://www.faperj.br/?id=28.5.7",
    "renderizacao": "server-side",
    "estrutura_listagem": "A listagem é de extrema dificuldade para scraping. O conteúdo é texto livre despejado no HTML cru sob a div principal. Cada edital começa com um texto bold ou hífens (- Edital FAPERJ Nº...), com datas soltas na mesma linha e links embutidos no meio do texto.",
    "pagina_individual": "Não há padrão de página individual. O conteúdo descritivo e o link direto para o PDF ou sistemas residem na própria página de listagem geral.",
    "formato_data": "dd/mm/aaaa (ex: '19/02/2026 a 13/03/2026')",
    "indicador_status": "Ruim. Tudo é jogado sob a categoria '1) EDITAIS 2026: LANÇAMENTOS / RESULTADOS'. Você não tem um status programático de 'Aberta'. É necessário fazer um parse do texto 'Submissão de propostas on-line: de DD/MM a DD/MM' e comparar com a data atual no seu código.",
    "tem_faq": "Sim. URL: https://www.faperj.br/?id=44.4.3 (SisFAPERJ FAQ)",
    "foco_deeptech": "Sim. Lança editais fortes para base tecnológica. Exemplos: 'Programa Nacional de Apoio à Geração de Empreendimentos Inovadores (Centelha 3 RJ)' e 'Apoio a Projetos de Inovação no Campo da Segurança Pública'.",
    "gotchas": "É o site mais frágil para se criar um scraper. Eles atualizam prazos editando o próprio texto e riscando as datas antigas usando tags HTML como `<strike>` ou `<s>` (ex: <s>19/03/2026</s> 28/05/2026). É necessário uma heurística de Regex avançada para ignorar datas riscadas e pegar o prazo válido mais recente."
  },
  {
    "nome": "FAPESC",
    "uf": "SC",
    "site_oficial": "https://fapesc.sc.gov.br",
    "url_listagem_editais": "https://fapesc.sc.gov.br/editais-abertos/",
    "renderizacao": "server-side",
    "estrutura_listagem": "Construído em WordPress. A listagem é feita no padrão comum de listagem de posts/páginas do WP. Cada item (post) contém o título (ex: 'Edital 08/2024 – Mulheres+Tec') e um pequeno excerto.",
    "pagina_individual": "Sim. Título linca para o post no WordPress correspondente, com toda a contextualização e links para downloads dos PDF de anexo.",
    "formato_data": "Geralmente no formato numérico dd/mm/aaaa inserido no corpo do texto livre ou a própria meta de publicação do post.",
    "indicador_status": "Fácil. A organização tem separações de URL para status vital. Basta varrer a rota `/editais-abertos/` e ignorar a `/editais-encerrados/`.",
    "tem_faq": "Não unificado (perguntas normalmente ficam restritas aos e-mails dos gestores do edital publicados nos PDFs).",
    "foco_deeptech": "Sim. Lança muitos editais para startups. Exemplos recentes: 'Mulheres+Tec', fomento para internacionalização de empresas e 'Centelha'.",
    "gotchas": "Ao contrário da FAPEMIG, a listagem não expõe campos meta em HTML (como a data limite de fechamento explícita fora do texto). Para pegar a data de encerramento do edital, o scraper terá que extrair via NLP ou OCR lendo de dentro do PDF correspondente."
  },
  {
    "nome": "FACEPE",
    "uf": "PE",
    "site_oficial": "https://www.facepe.br",
    "url_listagem_editais": "https://www.facepe.br/editais/todos/",
    "renderizacao": "server-side",
    "estrutura_listagem": "Renderização clássica do backend (WordPress). Exibe uma lista em HTML simples. A linha do bloco contém número (ex: '19/2026'), título, data ('Publicação: 8 de junho de 2026') e um botão link de 'Download'.",
    "pagina_individual": "A maioria não possui página com HTML embutido próprio; o fluxo clica e faz o download ou abre o próprio arquivo PDF no navegador.",
    "formato_data": "d de [mês extenso] de aaaa (ex: '8 de junho de 2026')",
    "indicador_status": "Excelente e programático. O site tem filtros claros: 'Abertos', 'Em julgamento', 'Resultados' e 'Encerrados'. Você pode fazer o scrape diretamente da URL: https://www.facepe.br/editais/abertos/",
    "tem_faq": "Sim. URL: https://www.facepe.br/fomento/duvidas-frequentes/",
    "foco_deeptech": "Sim, fomento pesado a empresas de TI. Exemplos recentes: 'Pró-Startups: Mulheres que Inovam', 'Compet Soluções', 'Pernambucanas Inovadoras' e 'Centelha 3 PE'.",
    "gotchas": "A listagem embutida mistura os lançamentos originais dos editais com os seus respectivos desdobramentos (erratas, adendos de cronograma e listas de resultados preliminares), exibidos com marcadores tipo bullet (`•`). O scraper precisa filtrar essas linhas atreladas de resultados para não confundir com editais novos."
  }
]

##ChatGPT
[
  {
    "nome": "FAPEMIG",
    "uf": "MG",
    "site_oficial": "https://fapemig.br",
    "url_listagem_editais": "https://fapemig.br/oportunidades/chamadas-e-editais",
    "renderizacao": "spa-js",
    "evidencia_renderizacao": "A página renderiza a listagem via chamada AJAX '/api/calls'. Em versões indexadas aparecem erros como: 'Erro ao carregar chamadas: [GET] /api/calls?page=1...' indicando dependência de JavaScript/API. O HTML inicial contém shell da página e filtros, mas a listagem é carregada dinamicamente.",
    "estrutura_listagem": {
      "descricao": "Cards de chamadas com status, categoria, público-alvo, datas e botão 'Saiba Mais'.",
      "exemplo_titulo": "Programa Centelha 3 - Programa Nacional de Apoio à Geração de Empreendimentos Inovadores",
      "exemplo_campos": [
        "status",
        "lançamento",
        "submissão até",
        "resultado previsto",
        "valor",
        "saiba mais"
      ]
    },
    "pagina_individual": {
      "possui_pagina_propria": true,
      "padrao_url": "URLs numéricas individuais sob fapemig.br",
      "conteudo": "Página HTML própria; normalmente complementada por anexos/PDFs.",
      "exemplo": "https://fapemig.br/central-de-ajuda/perguntas-frequentes/2916"
    },
    "formato_data": [
      "03 de jun. de 2026",
      "06 de jul. de 2026"
    ],
    "indicador_status": {
      "possui": true,
      "valores": [
        "Aberta",
        "Em análise",
        "Resultados",
        "Encerradas"
      ]
    },
    "tem_faq": {
      "possui": true,
      "url": "https://fapemig.br/central-de-ajuda/perguntas-frequentes/2916"
    },
    "foco_deeptech": {
      "possui_editais_empresa": true,
      "exemplos": [
        "Programa Centelha 3",
        "Novo SEED: Aceleração de Startups",
        "Minas pelo Clima: Ciência e Inovação",
        "Chamadas classificadas para 'Empresas'"
      ]
    },
    "gotchas": [
      "SPA/JS; scraper HTML simples pode não capturar a listagem.",
      "API interna aparenta alimentar os cards.",
      "Categorias e filtros alteram consultas dinâmicas.",
      "Sistema Everest é usado para submissão e exige login, mas a consulta pública dos editais não."
    ],
    "trechos_reais": [
      "Todas (179) Abertas (10) Em Análise (6) Resultados (29) Encerradas (134)",
      "Programa Centelha 3 - Programa Nacional de Apoio à Geração de Empreendimentos Inovadores"
    ],
    "fontes": [
      "https://fapemig.br/oportunidades/chamadas-e-editais"
    ]
  },
  {
    "nome": "FAPERJ",
    "uf": "RJ",
    "site_oficial": "https://www.faperj.br",
    "url_listagem_editais": "https://www.faperj.br/?id=28.5.7",
    "renderizacao": "server-side",
    "evidencia_renderizacao": "A página entrega diretamente no HTML a lista completa de editais com cronogramas, títulos e resultados, sem depender de renderização client-side visível. :contentReference[oaicite:0]{index=0}",
    "estrutura_listagem": {
      "descricao": "Lista textual longa organizada por ano, contendo título do edital seguido por cronograma.",
      "exemplo_titulo": "Edital FAPERJ Nº 01/2026 - Apoio à Recuperação e Modernização da Infraestrutura de Hospitais Universitários",
      "exemplo_campos": [
        "lançamento",
        "submissão",
        "resultado preliminar",
        "resultado final",
        "errata"
      ]
    },
    "pagina_individual": {
      "possui_pagina_propria": false,
      "conteudo": "Muitos editais aparecem inline na própria página anual. Links para PDFs e resultados podem existir dentro do texto.",
      "observacao": "Estrutura menos orientada a landing page individual do que outras FAPs."
    },
    "formato_data": [
      "19/02/2026",
      "13/03/2026",
      "a partir de 01/05/2026"
    ],
    "indicador_status": {
      "possui": true,
      "como": "Inferido por seções de lançamentos/resultados e cronogramas; não há selo visual padronizado ABERTA/ENCERRADA."
    },
    "tem_faq": {
      "possui": false,
      "url": null
    },
    "foco_deeptech": {
      "possui_editais_empresa": true,
      "exemplos": [
        "INFRALAB – Apoio à Infraestrutura de Ambientes de Inovação e Laboratórios Compartilhados para Startups",
        "Programa Doutor Empreendedor",
        "Bolsa de Inovação Tecnológica (INT)",
        "Prioridade Rio",
        "INOVATRIP"
      ]
    },
    "gotchas": [
      "URLs usam parâmetro '?id=' e não slugs amigáveis.",
      "Conteúdo histórico de vários anos concentrado em páginas extensas.",
      "Estrutura editorial heterogênea.",
      "Pode exigir parser específico para separar cronogramas de editais."
    ],
    "trechos_reais": [
      "Edital FAPERJ Nº 01/2026 - Apoio à Recuperação e Modernização da Infraestrutura de Hospitais Universitários",
      "Submissão de propostas on-line: 19/02/2026 a 13/03/2026"
    ],
    "fontes": [
      "https://www.faperj.br/?id=28.5.7"
    ]
  },
  {
    "nome": "FAPESC",
    "uf": "SC",
    "site_oficial": "https://fapesc.sc.gov.br",
    "url_listagem_editais": "https://fapesc.sc.gov.br/chamadas-abertas/",
    "renderizacao": "server-side",
    "evidencia_renderizacao": "A lista de chamadas abertas aparece diretamente no HTML retornado pelo servidor, incluindo títulos e datas. :contentReference[oaicite:1]{index=1}",
    "estrutura_listagem": {
      "descricao": "Posts WordPress categorizados como 'Chamadas abertas'.",
      "titulo_tag": "Heading do post",
      "link": "Cada edital possui URL própria.",
      "exemplo": "EDITAL DE CHAMADA PÚBLICA FAPESC N.º 35/2026 CREDENCIAMENTO DE INCUBADORAS"
    },
    "pagina_individual": {
      "possui_pagina_propria": true,
      "padrao_url": "https://fapesc.sc.gov.br/.../",
      "conteudo": "Texto HTML completo do edital e normalmente anexos PDF."
    },
    "formato_data": [
      "09/06/2026",
      "03/06/2026"
    ],
    "indicador_status": {
      "possui": true,
      "valores": [
        "Chamadas abertas",
        "Chamadas em andamento",
        "Divulgação de resultados",
        "Chamadas encerradas"
      ]
    },
    "tem_faq": {
      "possui": false,
      "url": null
    },
    "foco_deeptech": {
      "possui_editais_empresa": true,
      "exemplos": [
        "Acelera Startup SC",
        "Centelha 3 SC",
        "Credenciamento de Incubadoras",
        "Mulheres+Tec",
        "Subvenção Econômica Horizonte Europa"
      ]
    },
    "gotchas": [
      "WordPress relativamente simples de raspar.",
      "Mesmo edital pode existir em página de categoria e página individual.",
      "Mistura programas de pesquisa, inovação e bolsas."
    ],
    "trechos_reais": [
      "EDITAL DE CHAMADA PÚBLICA FAPESC N.º 31/2026 PROGRAMA ACELERA STARTUP SC – 6ª Edição",
      "EDITAL N.º 12/2026 PROGRAMA CENTELHA 3 – SC"
    ],
    "fontes": [
      "https://fapesc.sc.gov.br/chamadas-abertas/",
      "https://fapesc.sc.gov.br/category/chamadas-abertas/"
    ]
  },
  {
    "nome": "FACEPE",
    "uf": "PE",
    "site_oficial": "https://www.facepe.br",
    "url_listagem_editais": "https://www.facepe.br/editais/",
    "renderizacao": "server-side",
    "evidencia_renderizacao": "A listagem de editais é entregue diretamente no HTML com filtros e itens visíveis sem execução de JavaScript. :contentReference[oaicite:2]{index=2}",
    "estrutura_listagem": {
      "descricao": "Lista de editais com título, data de publicação e link 'Download'.",
      "exemplo": "07/2026 - Lançamento Edital nº 07/2026 – Pró-Startups: Mulheres que Inovam",
      "link_principal": "Download"
    },
    "pagina_individual": {
      "possui_pagina_propria": false,
      "conteudo": "Na maioria dos casos o edital é disponibilizado diretamente por PDF via botão Download.",
      "observacao": "Menos conteúdo HTML estruturado; PDF é a fonte principal."
    },
    "formato_data": [
      "5 de março de 2026",
      "11 de fevereiro de 2026"
    ],
    "indicador_status": {
      "possui": true,
      "valores": [
        "Abertos",
        "Em Julgamento",
        "Resultados",
        "Encerrados",
        "Todos"
      ]
    },
    "tem_faq": {
      "possui": false,
      "url": null
    },
    "foco_deeptech": {
      "possui_editais_empresa": true,
      "exemplos": [
        "Pró-Startups: Mulheres que Inovam",
        "Pernambucanas Inovadoras",
        "Compet Mulheres"
      ]
    },
    "gotchas": [
      "Grande dependência de PDFs para conteúdo completo.",
      "Necessário seguir links Download para extrair regras e prazos.",
      "Pouca padronização semântica além dos títulos."
    ],
    "trechos_reais": [
      "07/2026 - Lançamento Edital nº 07/2026 – Pró-Startups: Mulheres que Inovam",
      "06/2026 - Edital Nº 06/2026-Facepe Pernambucanas Inovadoras"
    ],
    "fontes": [
      "https://www.facepe.br/editais/"
    ]
  }
]

#Prompt B1

##Gemini
[
  {
    "id": "investidor:indicator-capital",
    "name": "Indicator Capital",
    "tese": "Foco em Internet das Coisas (IoT) e Deep Tech, buscando transformar inovação tecnológica profunda em negócios escaláveis e de impacto na América Latina.",
    "tese_themes": [
      "tecnologias digitais e conectividade",
      "materiais, química e manufatura avançada"
    ],
    "tese_keywords": [
      "iot",
      "deep-tech",
      "hardware",
      "b2b",
      "industry 4.0",
      "inteligência artificial"
    ],
    "setores": [
      "agro",
      "energia",
      "saude",
      "industria",
      "mobilidade",
      "ti-software"
    ],
    "estagio_alvo": [
      "seed",
      "serie-a"
    ],
    "ticket_range": {
      "min_brl": 5000000,
      "max_brl": 20000000
    },
    "lead_follow": "lead",
    "generalista": false,
    "anti_tese": "Modelos B2C puramente transacionais ou desenvolvimento de hardware sem um modelo claro de receita recorrente.",
    "fund_status": "ativo",
    "site": "https://indicatorcapital.com",
    "source_urls": [
      "https://indicatorcapital.com/"
    ],
    "verificado_em": "2026-06-16"
  },
  {
    "id": "investidor:sp-ventures",
    "name": "SP Ventures",
    "tese": "Investimento focado exclusivamente em tecnologias aplicadas ao agronegócio (AgTech) e alimentação (FoodTech), impulsionando a resiliência climática e a transição sustentável do setor.",
    "tese_themes": [
      "agro - bioeconomia e alimentos"
    ],
    "tese_keywords": [
      "agtech",
      "foodtech",
      "climate-tech",
      "biotech",
      "b2b",
      "farm-management"
    ],
    "setores": [
      "agro",
      "meio-ambiente"
    ],
    "estagio_alvo": [
      "seed",
      "serie-a"
    ],
    "ticket_range": {
      "min_brl": 5000000,
      "max_brl": 15000000
    },
    "lead_follow": "lead",
    "generalista": false,
    "anti_tese": "Setores fora da cadeia de valor do agronegócio, alimentos ou negócios sem base tecnológica defensável.",
    "fund_status": "ativo",
    "site": "https://spventures.com.br",
    "source_urls": [
      "https://spventures.com.br/"
    ],
    "verificado_em": "2026-06-16"
  },
  {
    "id": "investidor:kptl",
    "name": "KPTL",
    "tese": "Gestora de fundos temáticos focada em inovação aplicada, buscando startups que resolvem problemas reais e estruturais em setores regulados ou de alta complexidade no Brasil.",
    "tese_themes": [
      "tecnologias digitais e conectividade",
      "saúde e ciências da vida",
      "agro - bioeconomia e alimentos",
      "energia e transição sustentável"
    ],
    "tese_keywords": [
      "govtech",
      "agtech",
      "healthtech",
      "energy",
      "b2b",
      "b2g",
      "deep-tech"
    ],
    "setores": [
      "agro",
      "energia",
      "saude",
      "ti-software",
      "meio-ambiente",
      "multissetorial"
    ],
    "estagio_alvo": [
      "seed",
      "serie-a"
    ],
    "ticket_range": {
      "min_brl": 2000000,
      "max_brl": 15000000
    },
    "lead_follow": "ambos",
    "generalista": false,
    "anti_tese": "Startups que não possuam produto validado ou empresas sem visão de crescimento em escala compatível com venture capital.",
    "fund_status": "ativo",
    "site": "https://kptl.com.br",
    "source_urls": [
      "https://kptl.com.br/"
    ],
    "verificado_em": "2026-06-16"
  },
  {
    "id": "investidor:canary",
    "name": "Canary",
    "tese": "Atua como o primeiro parceiro institucional para os melhores fundadores da América Latina, investindo de forma agnóstica em negócios early-stage com imenso potencial de escala.",
    "tese_themes": [],
    "tese_keywords": [
      "agnostic",
      "early-stage",
      "b2b",
      "b2c",
      "fintech",
      "saas",
      "marketplace"
    ],
    "setores": [
      "multissetorial"
    ],
    "estagio_alvo": [
      "pre-seed",
      "seed"
    ],
    "ticket_range": {
      "min_brl": 2500000,
      "max_brl": 15000000
    },
    "lead_follow": "lead",
    "generalista": true,
    "anti_tese": "Projetos sem ambição e potencial de formar empresas de classe mundial (venture scale) ou fundadores sem dedicação exclusiva ao negócio.",
    "fund_status": "ativo",
    "site": "https://canary.com.br",
    "source_urls": [
      "https://canary.com.br/"
    ],
    "verificado_em": "2026-06-16"
  },
  {
    "id": "investidor:astella",
    "name": "Astella",
    "tese": "Foco em jornadas de empreendedores criando soluções eficientes de capital, privilegiando modelos B2B, SaaS e marketplaces com economics claros desde o início.",
    "tese_themes": [],
    "tese_keywords": [
      "b2b",
      "saas",
      "marketplaces",
      "plg",
      "metrics-driven",
      "software"
    ],
    "setores": [
      "ti-software",
      "multissetorial"
    ],
    "estagio_alvo": [
      "seed",
      "serie-a"
    ],
    "ticket_range": {
      "min_brl": 5000000,
      "max_brl": 25000000
    },
    "lead_follow": "lead",
    "generalista": true,
    "anti_tese": "Empresas B2C intensivas em capital ou operações onde a eficiência de aquisição de clientes (CAC/LTV) não seja o motor de crescimento.",
    "fund_status": "ativo",
    "site": "https://astellainvest.com",
    "source_urls": [
      "https://astellainvest.com/"
    ],
    "verificado_em": "2026-06-16"
  }
]

##ChatGPT
[
{
"id": "investidor:kptl",
"name": "KPTL",
"tese": "Investe em negócios de inovação de alto impacto com tecnologia proprietária, alinhados a megatendências como clima, IA, bioeconomia, saúde e transformação digital. Busca startups com diferenciais competitivos sustentáveis e base tecnológica robusta.",
"tese_themes": [
"agro - bioeconomia e alimentos",
"energia e transição sustentável",
"saúde e ciências da vida",
"tecnologias digitais e conectividade"
],
"tese_keywords": ["deep-tech", "biotech", "ia", "saas", "iot", "hardware", "climate tech", "govtech"],
"setores": ["agro", "energia", "saude", "meio-ambiente", "ti-software", "multissetorial"],
"estagio_alvo": ["seed", "serie-a"],
"ticket_range": null,
"lead_follow": "ambos",
"generalista": false,
"anti_tese": "Não investe em negócios sem inovação tecnológica clara ou sem diferenciais defensáveis de longo prazo.",
"fund_status": "ativo",
"site": "[https://www.kptl.com.br/](https://www.kptl.com.br/)",
"source_urls": [
"[https://www.kptl.com.br/](https://www.kptl.com.br/)",
"[https://www.kptl.com.br/sobre-kptl/](https://www.kptl.com.br/sobre-kptl/)",
"[https://www.kptl.com.br/fundo-venture-capital/](https://www.kptl.com.br/fundo-venture-capital/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:indicator-capital",
"name": "Indicator Capital",
"tese": "Fundo especializado em deep-tech focado em tecnologias inteligentes e conectadas, incluindo IA, IoT, infraestrutura, hardware e software. Investe em startups que impulsionam a transformação digital em escala global.",
"tese_themes": [
"tecnologias digitais e conectividade"
],
"tese_keywords": ["deep-tech", "ia", "iot", "hardware", "software", "infraestrutura digital", "conectividade"],
"setores": ["ti-software", "industria"],
"estagio_alvo": ["seed", "serie-a"],
"ticket_range": null,
"lead_follow": "lead",
"generalista": false,
"anti_tese": "Não possui foco em negócios tradicionais sem componente tecnológico avançado.",
"fund_status": "ativo",
"site": "[https://indicator.capital/](https://indicator.capital/)",
"source_urls": [
"[https://indicator.capital/about](https://indicator.capital/about)",
"[https://indicator.capital/pt/about](https://indicator.capital/pt/about)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:sp-ventures",
"name": "SP Ventures",
"tese": "Investe em soluções tecnológicas para agricultura, alimentos, clima e sustentabilidade na América Latina. Busca inovação que aumente a resiliência climática e a segurança alimentar.",
"tese_themes": [
"agro - bioeconomia e alimentos",
"energia e transição sustentável"
],
"tese_keywords": ["agtech", "foodtech", "climate tech", "biologicals", "agrofintech", "sustentabilidade"],
"setores": ["agro", "energia", "meio-ambiente"],
"estagio_alvo": ["seed", "serie-a"],
"ticket_range": null,
"lead_follow": "lead",
"generalista": false,
"anti_tese": "Não tem foco em startups fora da cadeia agroalimentar e dos desafios climáticos relacionados.",
"fund_status": "ativo",
"site": "[https://spventures.com.br/](https://spventures.com.br/)",
"source_urls": [
"[https://spventures.com.br/](https://spventures.com.br/)",
"[https://www.spventures.com.br/](https://www.spventures.com.br/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:maya-capital",
"name": "MAYA Capital",
"tese": "Investe na primeira rodada institucional de startups latino-americanas de alto potencial. Atua como fundo generalista com foco em tecnologia e transformação de mercados.",
"tese_themes": [],
"tese_keywords": ["software", "internet", "b2b", "b2c", "marketplaces", "fintech"],
"setores": ["multissetorial"],
"estagio_alvo": ["pre-seed", "seed"],
"ticket_range": null,
"lead_follow": "lead",
"generalista": true,
"anti_tese": "Não foca em estágios avançados de crescimento como principal estratégia.",
"fund_status": "ativo",
"site": "[https://www.maya.capital/](https://www.maya.capital/)",
"source_urls": [
"[https://www.maya.capital/about-us](https://www.maya.capital/about-us)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:astella",
"name": "Astella",
"tese": "Fundo de venture capital focado em tecnologia e construção de empresas escaláveis na América Latina. Atua de forma setorialmente ampla.",
"tese_themes": [],
"tese_keywords": ["software", "saas", "marketplaces", "b2b", "tecnologia"],
"setores": ["multissetorial"],
"estagio_alvo": ["seed", "serie-a"],
"ticket_range": null,
"lead_follow": "ambos",
"generalista": true,
"anti_tese": "Não possui tese temática restrita a um setor específico.",
"fund_status": "ativo",
"site": "[https://astella.com.br/](https://astella.com.br/)",
"source_urls": [
"[https://astella.com.br/](https://astella.com.br/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:canary",
"name": "Canary",
"tese": "Investe em fundadores na fase mais inicial da jornada empreendedora no Brasil. Atua como fundo generalista de tecnologia.",
"tese_themes": [],
"tese_keywords": ["pre-seed", "software", "internet", "tecnologia", "founder-first"],
"setores": ["multissetorial"],
"estagio_alvo": ["pre-seed", "seed"],
"ticket_range": null,
"lead_follow": "lead",
"generalista": true,
"anti_tese": "Não é focado em growth ou rodadas tardias.",
"fund_status": "ativo",
"site": "[https://canary.com.br/](https://canary.com.br/)",
"source_urls": [
"[https://canary.com.br/](https://canary.com.br/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:norte-ventures",
"name": "Norte Ventures",
"tese": "Investe em startups tecnológicas brasileiras em estágio inicial com potencial de crescimento relevante. Atua de forma multissetorial.",
"tese_themes": [],
"tese_keywords": ["software", "b2b", "saas", "marketplaces", "tech"],
"setores": ["multissetorial"],
"estagio_alvo": ["pre-seed", "seed"],
"ticket_range": null,
"lead_follow": "ambos",
"generalista": true,
"anti_tese": "Não possui foco temático exclusivo em um vertical específico.",
"fund_status": "ativo",
"site": "[https://norte.ventures/](https://norte.ventures/)",
"source_urls": [
"[https://norte.ventures/](https://norte.ventures/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:oria-capital",
"name": "Oria Capital",
"tese": "Investe em empresas de tecnologia B2B com software escalável e crescimento acelerado. Prioriza companhias com produtos já validados.",
"tese_themes": [
"tecnologias digitais e conectividade"
],
"tese_keywords": ["b2b", "software", "saas", "enterprise software", "cloud"],
"setores": ["ti-software"],
"estagio_alvo": ["serie-a", "growth"],
"ticket_range": null,
"lead_follow": "lead",
"generalista": false,
"anti_tese": "Não foca em negócios não tecnológicos ou em estágio muito inicial.",
"fund_status": "ativo",
"site": "[https://oriacapital.com/](https://oriacapital.com/)",
"source_urls": [
"[https://oriacapital.com/](https://oriacapital.com/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:positive-ventures",
"name": "Positive Ventures",
"tese": "Investe em startups que combinam retorno financeiro com impacto socioambiental positivo. Busca inovação tecnológica voltada a grandes desafios globais.",
"tese_themes": [
"energia e transição sustentável",
"saúde e ciências da vida",
"agro - bioeconomia e alimentos"
],
"tese_keywords": ["impacto", "climate tech", "healthtech", "edtech", "deep-tech"],
"setores": ["saude", "energia", "meio-ambiente", "agro"],
"estagio_alvo": ["seed", "serie-a"],
"ticket_range": null,
"lead_follow": "ambos",
"generalista": false,
"anti_tese": "Não investe em negócios sem alinhamento com impacto positivo mensurável.",
"fund_status": "ativo",
"site": "[https://positiveventures.com/](https://positiveventures.com/)",
"source_urls": [
"[https://positiveventures.com/](https://positiveventures.com/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:vox-capital",
"name": "VOX Capital",
"tese": "Investe em empresas que resolvem desafios sociais e ambientais relevantes por meio de inovação e tecnologia.",
"tese_themes": [
"saúde e ciências da vida",
"energia e transição sustentável"
],
"tese_keywords": ["impact investing", "healthtech", "climate tech", "inovação social"],
"setores": ["saude", "energia", "meio-ambiente"],
"estagio_alvo": ["seed", "serie-a"],
"ticket_range": null,
"lead_follow": "ambos",
"generalista": false,
"anti_tese": "Não foca em empresas sem potencial de impacto socioambiental relevante.",
"fund_status": "ativo",
"site": "[https://voxcapital.com.br/](https://voxcapital.com.br/)",
"source_urls": [
"[https://voxcapital.com.br/](https://voxcapital.com.br/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:antler-brasil",
"name": "Antler",
"tese": "Investe e cofundadores em startups desde a fase de formação da empresa, apoiando empreendedores de tecnologia em estágio inicial.",
"tese_themes": [],
"tese_keywords": ["day-zero", "pre-seed", "technology", "startup studio", "founders"],
"setores": ["multissetorial"],
"estagio_alvo": ["pre-seed", "seed"],
"ticket_range": null,
"lead_follow": "lead",
"generalista": true,
"anti_tese": "Não é focado em rodadas growth ou empresas maduras.",
"fund_status": "ativo",
"site": "[https://www.antler.co/](https://www.antler.co/)",
"source_urls": [
"[https://www.antler.co/](https://www.antler.co/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:bossa-invest",
"name": "Bossa Invest",
"tese": "Investe em um grande volume de startups de tecnologia em estágio inicial, com abordagem diversificada e foco em empreendedorismo.",
"tese_themes": [],
"tese_keywords": ["anjo", "pre-seed", "seed", "tech startups"],
"setores": ["multissetorial"],
"estagio_alvo": ["pre-seed", "seed"],
"ticket_range": null,
"lead_follow": "follow",
"generalista": true,
"anti_tese": "Não possui foco exclusivo em deep-tech ou em um setor específico.",
"fund_status": "ativo",
"site": "[https://bossainvest.com/](https://bossainvest.com/)",
"source_urls": [
"[https://bossainvest.com/](https://bossainvest.com/)"
],
"verificado_em": "2026-06-16"
},
{
"id": "investidor:domo-vc",
"name": "DOMO.VC",
"tese": "Investe em startups brasileiras de tecnologia com potencial de crescimento e escalabilidade. Atua em diferentes verticais digitais.",
"tese_themes": [],
"tese_keywords": ["software", "saas", "digital", "b2b", "tech"],
"setores": ["multissetorial"],
"estagio_alvo": ["seed", "serie-a"],
"ticket_range": null,
"lead_follow": "ambos",
"generalista": true,
"anti_tese": "Não possui foco exclusivo em um setor industrial específico.",
"fund_status": "ativo",
"site": "[https://domo.vc/](https://domo.vc/)",
"source_urls": [
"[https://domo.vc/](https://domo.vc/)"
],
"verificado_em": "2026-06-16"
}
]


#Prompt B2

##Gemini
```json
[
  {
    "id": "programa:centelha",
    "name": "Programa Centelha",
    "operador": "MCTI/FINEP/FAPs",
    "tipo": "subvencao",
    "descricao": "Programa de estímulo à criação de empreendimentos inovadores, oferecendo capacitação, recursos financeiros não reembolsáveis e suporte para transformar ideias em negócios. Operado nacionalmente com editais estadualizados pelas Fundações de Amparo à Pesquisa (FAPs).",
    "formato": "edital-periodico",
    "cadencia": "anual",
    "beneficio": "Subvenção econômica (capital não-reembolsável), bolsas de fomento, mentorias e capacitação empreendedora.",
    "ticket_range": {
      "min_brl": 50000,
      "max_brl": 130000
    },
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": [
      "pre-seed"
    ],
    "elegibilidade": "Pessoas físicas e empresas com faturamento de até R$ 4,8 milhões, criadas em até 12 meses antes do lançamento do edital estadual.",
    "site": "https://programacentelha.com.br",
    "faq_url": "https://programacentelha.com.br/duvidas-frequentes/",
    "source_urls": [
      "https://programacentelha.com.br/o-programa/"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:sebrae-startups",
    "name": "Sebrae Startups",
    "operador": "Sebrae",
    "tipo": "capacitacao",
    "descricao": "Plataforma e hub de iniciativas do Sebrae para apoiar o ecossistema de startups no Brasil. Engloba diversos desafios regionais e setoriais de capacitação, conexão com grandes corporações e suporte técnico.",
    "formato": "fluxo-continuo",
    "cadencia": "continuo",
    "beneficio": "Mentorias, trilhas de capacitação, conexão com o mercado, acesso a infraestrutura tecnológica (ex: créditos de nuvem) e suporte do programa Sebraetec.",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": [
      "pre-seed",
      "seed"
    ],
    "elegibilidade": "Startups formalizadas (MEI, ME ou EPP) com faturamento até R$ 4,8 milhões anuais, focadas em inovação e modelos de negócios escaláveis.",
    "site": "https://www.sebraestartups.com.br",
    "faq_url": "https://www.sebraestartups.com.br/faq",
    "source_urls": [
      "https://www.sebraestartups.com.br/sobre-nos"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:bndes-garagem",
    "name": "BNDES Garagem",
    "operador": "BNDES",
    "tipo": "aceleracao",
    "descricao": "Iniciativa do BNDES de fomento a startups de impacto (social ou ambiental) no Brasil. O programa possui duas trilhas de aceleração: Criação e Tração, apoiando empreendedores na consolidação de soluções escaláveis.",
    "formato": "cohort",
    "cadencia": "anual",
    "beneficio": "Aceleração equity-free, mentorias especializadas, suporte jurídico/contábil, acompanhamento de métricas e conexão com o ecossistema de impacto e corporações.",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": [
      "pre-seed",
      "seed"
    ],
    "elegibilidade": "Startups que resolvam problemas socioambientais (saúde, educação, meio ambiente, etc.), com CNPJ ativo no Brasil, enquadradas nas fases de criação (validação) ou tração (já com faturamento ou usuários ativos).",
    "site": "https://www.bndes.gov.br/garagem",
    "faq_url": "https://garagem.bndes.gov.br/faq",
    "source_urls": [
      "https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/inovacao/bndes-garagem"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:bndes-funtec",
    "name": "BNDES FUNTEC",
    "operador": "BNDES",
    "tipo": "subvencao",
    "descricao": "Fundo de Desenvolvimento Tecnológico que apoia financeiramente projetos de pesquisa aplicada, desenvolvimento experimental e inovação tecnológica. Visa fortalecer a parceria entre empresas e Instituições Tecnológicas (ICTs).",
    "formato": "fluxo-continuo",
    "cadencia": "continuo",
    "beneficio": "Apoio financeiro não reembolsável direcionado à Instituição Tecnológica (ICT), mediante contrapartida financeira e parceria com a Empresa Interveniente que introduzirá a inovação no mercado.",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": [
      "seed",
      "serie-a",
      "growth"
    ],
    "elegibilidade": "Instituições Tecnológicas (ICTs) atuando obrigatoriamente em parceria com empresas sediadas no Brasil que possuam capacidade de industrialização ou comercialização do resultado do projeto.",
    "site": "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/funtec",
    "faq_url": "https://www.bndes.gov.br/wps/portal/site/home/quem-somos/canais-atendimento/perguntas-frequentes/funtec",
    "source_urls": [
      "https://www.bndes.gov.br/wps/portal/site/home/financiamento/produto/funtec"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:inovativa-brasil",
    "name": "InovAtiva Brasil",
    "operador": "MDIC / Sebrae",
    "tipo": "aceleracao",
    "descricao": "O maior e mais abrangente programa de aceleração de startups da América Latina, sem cobrança de equity. Focado na capacitação, mentoria e conexão de startups em diferentes estágios com o mercado e investidores.",
    "formato": "cohort",
    "cadencia": "2x/ano",
    "beneficio": "Capacitação massiva (online), mentorias individuais equity-free, treinamento de pitch e apresentação em Demoday (InovAtiva Experience) para investidores.",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": [
      "pre-seed",
      "seed"
    ],
    "elegibilidade": "Startups de todos os setores e regiões do Brasil em fase de ideação/validação (InovAtiva de Impacto e Ideação), operação ou tração.",
    "site": "https://www.inovativabrasil.com.br",
    "faq_url": "https://www.inovativabrasil.com.br/faq/",
    "source_urls": [
      "https://www.inovativabrasil.com.br/sobre-nos/"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:catalisa-ict",
    "name": "Catalisa ICT",
    "operador": "Sebrae",
    "tipo": "aceleracao",
    "descricao": "Iniciativa articulada pelo Sebrae focada em acelerar a transferência de tecnologia da academia para o mercado. Oferece jornadas de capacitação, bolsas de pesquisa e auxílio na abertura de empresas deep tech.",
    "formato": "edital-periodico",
    "cadencia": "anual",
    "beneficio": "Bolsas de pesquisa para dedicação ao plano de inovação, mentorias, capacitação em gestão da inovação e acesso posterior a subvenção econômica e conexão com fundos.",
    "ticket_range": {
      "min_brl": 50000,
      "max_brl": 150000
    },
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": [
      "pre-seed"
    ],
    "elegibilidade": "Pesquisadores vinculados a Instituições Científicas, Tecnológicas e de Inovação (ICTs) com pesquisas de alto potencial mercadológico e tecnológico.",
    "site": "https://sebrae.com.br/sites/PortalSebrae/catalisaict",
    "faq_url": "https://sebrae.com.br/sites/PortalSebrae/catalisaict",
    "source_urls": [
      "https://sebrae.com.br/sites/PortalSebrae/catalisaict"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:pipe-fapesp",
    "name": "Pesquisa Inovativa em Pequenas Empresas (PIPE)",
    "operador": "FAPESP",
    "tipo": "subvencao",
    "descricao": "Um dos principais e mais antigos programas para deep-tech do país, focado na pesquisa tecnológica em pequenas empresas. O programa divide-se em Fases de prova de conceito (1), desenvolvimento do produto (2) e desenvolvimento comercial (3).",
    "formato": "fluxo-continuo",
    "cadencia": "continuo",
    "beneficio": "Subvenção econômica (recursos não reembolsáveis) de alto valor para cobrir custos de pesquisa, aquisição de equipamentos e bolsas para pesquisadores alocados no projeto.",
    "ticket_range": {
      "min_brl": 300000,
      "max_brl": 1500000
    },
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": [
      "pre-seed",
      "seed",
      "serie-a"
    ],
    "elegibilidade": "Micro e pequenas empresas de base tecnológica com até 250 funcionários e sediadas no estado de São Paulo.",
    "site": "https://fapesp.br/pipe",
    "faq_url": "https://fapesp.br/pipe/perguntasfrequentes",
    "source_urls": [
      "https://fapesp.br/pipe/sobre"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  }
]

```

##ChatGPT
[
  {
    "id": "programa:centelha",
    "name": "Programa Centelha",
    "operador": "MCTI, Finep e Fundação CERTI (execução descentralizada via FAPs estaduais)",
    "tipo": "subvencao",
    "descricao": "Programa nacional de estímulo à criação de startups de base tecnológica a partir da transformação de ideias inovadoras em negócios. Opera por ciclos estaduais recorrentes com seleção competitiva, capacitação e subvenção econômica.",
    "formato": "edital-periodico",
    "cadencia": "anual",
    "beneficio": "capital não reembolsável, capacitação, mentorias e apoio à estruturação do negócio",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["pre-seed"],
    "elegibilidade": "empreendedores e startups em estágio inicial com proposta inovadora de base tecnológica, conforme regras de cada edição estadual",
    "site": "https://programacentelha.com.br",
    "faq_url": null,
    "source_urls": [
      "https://programacentelha.com.br"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:inovativa-brasil",
    "name": "InovAtiva Brasil",
    "operador": "MDIC e Sebrae",
    "tipo": "aceleracao",
    "descricao": "Programa nacional gratuito de aceleração para startups inovadoras. Realiza ciclos recorrentes de capacitação, mentorias, conexões com investidores e preparação para captação.",
    "formato": "cohort",
    "cadencia": "2x/ano",
    "beneficio": "mentoria, capacitação, conexão com investidores, networking e visibilidade",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["pre-seed", "seed"],
    "elegibilidade": "startups inovadoras brasileiras com CNPJ e solução em desenvolvimento ou validação",
    "site": "https://www.inovativabrasil.com.br",
    "faq_url": "https://www.inovativabrasil.com.br",
    "source_urls": [
      "https://www.inovativabrasil.com.br"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:catalisa-ict",
    "name": "Catalisa ICT",
    "operador": "Sebrae",
    "tipo": "capacitacao",
    "descricao": "Programa voltado à transformação de pesquisas acadêmicas em negócios inovadores. Apoia pesquisadores na jornada de empreendedorismo tecnológico por meio de capacitação, mentorias e conexão com o mercado.",
    "formato": "cohort",
    "cadencia": "anual",
    "beneficio": "capacitação, mentorias, conexão com investidores e apoio à criação de startups deep-tech",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["pre-seed"],
    "elegibilidade": "mestres, doutores, pesquisadores e empreendedores vinculados a ICTs",
    "site": "https://sebrae.com.br/sites/PortalSebrae/catalisaict",
    "faq_url": null,
    "source_urls": [
      "https://sebrae.com.br/sites/PortalSebrae/catalisaict"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:sebrae-startups",
    "name": "Sebrae Startups",
    "operador": "Sebrae",
    "tipo": "aceleracao",
    "descricao": "Plataforma nacional de desenvolvimento de startups com programas recorrentes de aceleração, capacitação e acesso a mercado. Atua como guarda-chuva para múltiplas trilhas e iniciativas do Sebrae.",
    "formato": "fluxo-continuo",
    "cadencia": "contínuo",
    "beneficio": "capacitação, mentorias, conexões comerciais, acesso a eventos e investidores",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["pre-seed", "seed"],
    "elegibilidade": "startups brasileiras em diferentes estágios de maturidade",
    "site": "https://sebraestartups.com.br",
    "faq_url": null,
    "source_urls": [
      "https://sebraestartups.com.br"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:bndes-garagem",
    "name": "BNDES Garagem",
    "operador": "BNDES",
    "tipo": "aceleracao",
    "descricao": "Programa de aceleração para negócios de impacto que apoia startups em estágios de criação e tração. A terceira edição prevê quatro ciclos de aceleração até 2028, com seleção recorrente de startups em todo o Brasil.",
    "formato": "cohort",
    "cadencia": "anual",
    "beneficio": "mentorias, capacitação, conexões com investidores, networking e premiações financeiras sem exigência de equity",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["pre-seed", "seed"],
    "elegibilidade": "negócios de impacto com potencial de geração de impacto socioambiental positivo e retorno financeiro",
    "site": "https://garagem.bndes.gov.br",
    "faq_url": "https://garagem.bndes.gov.br/tire-suas-duvidas/",
    "source_urls": [
      "https://garagem.bndes.gov.br/sobre/",
      "https://garagem.bndes.gov.br/tire-suas-duvidas/",
      "https://www.bndes.gov.br/wps/portal/site/home/onde-atuamos/inovacao/garagem/"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:bndes-funtec",
    "name": "BNDES Funtec",
    "operador": "BNDES",
    "tipo": "subvencao",
    "descricao": "Fundo tecnológico do BNDES destinado ao apoio não reembolsável a projetos de pesquisa aplicada, desenvolvimento tecnológico e inovação. Atua continuamente apoiando projetos executados por ICTs em parceria com empresas.",
    "formato": "fluxo-continuo",
    "cadencia": "contínuo",
    "beneficio": "apoio financeiro não reembolsável para projetos de P&D e inovação",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["pre-seed", "seed"],
    "elegibilidade": "instituições tecnológicas e de pesquisa em parceria com empresas aderentes aos focos estratégicos do programa",
    "site": "https://www.bndes.gov.br",
    "faq_url": null,
    "source_urls": [
      "https://www.bndes.gov.br/wps/portal/site/home/financiamento/plano-inova-empresa/plano-conjunto-bndes-finep-apoio-inovacao-tecnologica-industrial-setores-sucroenergetico-sucroquimico-paiss/plano-conjunto-bndes-finep-apoio-inovacao-tecnologica-industrial-setores-sucroenergetico-sucroquimico-paiss?1dmy=&urile=wcm%3Apath%3A%2Fbndes_institucional%2Fhome%2Ffinanciamentos%2Flinhas-programas-fundos%2Fbndes-funtec"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:startup-outreach-brasil",
    "name": "Startup OutReach Brasil",
    "operador": "ApexBrasil e Sebrae",
    "tipo": "capacitacao",
    "descricao": "Programa recorrente de internacionalização de startups brasileiras. Oferece preparação para expansão internacional, acesso a mercados externos e conexões com investidores e parceiros globais.",
    "formato": "cohort",
    "cadencia": "anual",
    "beneficio": "capacitação para internacionalização, mentorias e conexões internacionais",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["seed", "serie-a"],
    "elegibilidade": "startups brasileiras com produto validado e interesse em expansão internacional",
    "site": "https://www.apexbrasil.com.br",
    "faq_url": null,
    "source_urls": [
      "https://www.apexbrasil.com.br"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:finep-startup",
    "name": "Finep Startup",
    "operador": "Finep",
    "tipo": "subvencao",
    "descricao": "Programa recorrente da Finep para investimento em startups tecnológicas de alto potencial. Realiza chamadas periódicas com foco em empresas inovadoras em estágio inicial.",
    "formato": "edital-periodico",
    "cadencia": "anual",
    "beneficio": "aporte de capital para crescimento e desenvolvimento tecnológico",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["seed"],
    "elegibilidade": "startups inovadoras com CNPJ e tecnologia própria conforme critérios de cada chamada",
    "site": "https://www.finep.gov.br",
    "faq_url": null,
    "source_urls": [
      "https://www.finep.gov.br"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  },
  {
    "id": "programa:tecnova",
    "name": "TECNOVA",
    "operador": "Finep e Fundações Estaduais de Amparo à Pesquisa",
    "tipo": "subvencao",
    "descricao": "Programa de subvenção econômica para micro e pequenas empresas inovadoras executado em parceria com FAPs estaduais. Possui múltiplas edições ao longo dos anos e foco na inovação tecnológica empresarial.",
    "formato": "edital-periodico",
    "cadencia": "anual",
    "beneficio": "subvenção econômica não reembolsável para inovação",
    "ticket_range": null,
    "tese_themes": [],
    "setores": [],
    "estagio_alvo": ["seed"],
    "elegibilidade": "micro e pequenas empresas inovadoras conforme regras estaduais",
    "site": "https://www.finep.gov.br",
    "faq_url": null,
    "source_urls": [
      "https://www.finep.gov.br"
    ],
    "status": "ativo",
    "verificado_em": "2026-06-16"
  }
]

