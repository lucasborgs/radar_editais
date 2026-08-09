# SCV1-T04 — Caminho aberto: desafio corporativo e inovação aberta

## 1. Objetivo e resultado perceptível

Provar que o consultor não depende de um edital formal. A partir do mesmo projeto confirmado, ele pode descobrir um desafio corporativo ou oportunidade de inovação aberta em páginas web e múltiplas fontes, apresentar a qualidade/recência da descoberta e recomendar uma abordagem de mercado concreta, mesmo quando não existe prazo, formulário ou regra de elegibilidade.

## 2. Estado atual encontrado

- `opportunity_discovery.py`/`crawl4ai_discovery.py` fazem descoberta web e escrevem em `discovered_opportunities`; promoção humana grava bronze e segue o ETL gold.
- `deep_research` roda subagente web, devolve fontes e pode persistir `research_findings` pendentes; promoção para a biblioteca também tem gate humano.
- `entity_catalog` e `match_v3` pressupõem entidades gold (`edital`/`programa`) e documentos/chunks; `domain_paths` reconhece `desafio`, mas o caminho ainda é um card derivado de entidade.
- `WritingSession` deriva gênero e contexto por `edital_id`; não há representação de canal de inovação sem edital formal.
- A especificação exige recall na exploração, mas não autoriza transformar Discovery ou regras determinísticas em agentes.

## 3. Escopo funcional

- Definir uma entrada `CanalInovacao`/`Oportunidade` aberta que possa referenciar página, promotor, desafio, fontes corroborantes, data de coleta e estado de revisão sem fingir que é edital.
- Adaptar `DocumentIntelligence` para consumir o pacote atual de descoberta/pesquisa: documento canônico, fatos extraídos, evidências, freshness e confiança; promoção continua humana/determinística.
- Implementar `Knowledge` para recuperar achados promovidos/curados e `Pathways.propose(mode="open")` para conectar problema, solução, estágio e formato de participação.
- Criar `CaminhoInovacao` aberto com atores, fatos, inferências, lacunas e próximo passo de mercado (contato, validação, inscrição ou preparação de abordagem).
- Exibir claramente “sem edital formal”, “prazo não informado”, “fonte pendente” e “não é elegibilidade”; permitir que o usuário peça pesquisa adicional sem publicar automaticamente um fato.
- Fazer a vertical atravessar conversa, brief, projeto, caminhos e escolha; a execução mínima é o próximo passo de mercado, não candidatura ou submissão automática.

## 4. Contratos introduzidos ou alterados

- `DocumentIntelligence.ingest` passa a devolver `SourceDocumentRef`, `claims`, `evidence`, `freshness` e `review_state`; continua podendo usar `source_docs`/`research_findings` como adapters.
- `KnowledgeSignal` admite `kind=channel|opportunity`, `formal_instrument=false`, fonte primária/secundária e validade `unknown|needs_review|active` sem converter ausência de prazo em contínuo.
- `CaminhoInovacao.kind="open_innovation"` registra promotor/atores, fontes, problema de mercado, formato de participação, requisitos suaves, confiança e próximo passo.
- `Pathways.propose` recebe `mode`/objetivo e deve devolver explicação por evidência e qualidade da fonte; nenhum resultado de pesquisa web vira fato sem o estado de revisão correspondente.
- Evento de execução `market_next_step` deve ser distinto de `writing_session` e de `application_log` de edital.

## 5. Módulos provavelmente afetados

- `opportunity_discovery.py`, `discovery_materializer.py`, `discovery_evidence.py`, `discovery_promotion.py`, `research_tools.py`, `research.py` e `source_docs.py`.
- `entity_catalog.py`/gold para achados promovidos, novo adapter `DocumentIntelligence`, `Knowledge` e `Pathways`.
- `ConsultantGraph`, tipos/routers de caminhos e frontend de detalhes/próximo passo.
- Testes `opportunity_type`, `provenance`, `e2e_health`, `explore`/pesquisa, mais fixture de página aberta representativa.

## 6. Dependências

- `SCV1-T02` para projeto confirmado e `SCV1-T03` para os contratos ricos de evidência/temporalidade.
- Um caso de desafio aberto real ou fixture capturada com fonte, promotor e ausência explícita de edital formal.
- Gate existente de revisão humana; não abrir publicação automática.

## 7. Passos de implementação em ordem

1. Escolher um caso aberto representativo e separar página primária, corroborantes, fatos, inferências e lacunas de contato/prazo.
2. Modelar o pacote documental/canal sem duplicar `SourceBundle`; mapear staging, `research_findings` e promoção para a interface `DocumentIntelligence`.
3. Criar o adapter `Knowledge` para achados revisados e a política de freshness/estado conservador.
4. Implementar `Pathways.propose(mode="open")` com uma estratégia determinística de seleção de fonte/estado e LLM apenas para síntese explicável, não para publicar fato.
5. Ligar `ConsultantGraph` e UI para propor, investigar, mostrar fontes e registrar o próximo passo de mercado.
6. Executar o smoke aberto até a escolha e a ação; comparar a experiência com o caminho normativo sem criar uma arquitetura separada.
7. Retirar o pressuposto de `edital_id` deste ramo e promover o novo contrato aberto como única representação de desafio.

## 8. Comportamento legado a remover

- Remover `domain_paths`/Match como origem obrigatória de todo caminho aberto; busca de desafio não precisa passar pelo Stage 0–3 ou por `edital_id`.
- Remover `deep_research` como texto solto perdido no transcript; seus findings devem entrar pelo estado de revisão e pelo contrato documental quando forem usados pelo caminho.
- Retirar a apresentação de uma página web descoberta como “edital”, “prazo contínuo” ou “elegível” sem evidência.
- Retirar o salto para `/writing/start` com id sintético para representar um desafio; a ação padrão é `market_next_step`, e escrita só ocorre via T06 se o caminho oferecer artefato compatível.
- Não manter uma segunda consulta de Explore especial para web; o `ConsultantGraph` escolhe o adapter de pesquisa dentro do mesmo estado.

## 9. Critérios de aceite verificáveis

- Um caso sem edital formal aparece como caminho `open_innovation`, com promotor, fonte(s), estado de revisão e próximo passo de mercado.
- O sistema comunica explicitamente quando não há prazo/formulário; nunca afirma fluxo contínuo ou elegibilidade por ausência de informação.
- Pesquisa adicional mostra fontes e permanece draft/pendente até a revisão exigida; Discovery não publica direto no KG.
- O caminho aberto referencia o mesmo `ProjetoInovacao` e perfil do caminho normativo e pode ser selecionado/retomado.
- A UI diferencia oportunidade pública normativa, canal aberto, ICT/parceiro e documento de apoio.
- Nenhum crédito, investidor ou bolsa acadêmica aparece como alternativa aberta.

## 10. Validação proporcional

- Testes focais de estado sem edital, freshness, revisão humana, fonte primária/corroborante e próximo passo.
- Smoke com fixture/caso aberto: conversa → projeto → pesquisa/achado → caminho → escolha → ação de mercado.
- Rodar avaliação focal de `explore`, `provenance`, `opportunity_type` e `e2e_health`; não criar suíte paralela.
- `ruff check`, `npx tsc --noEmit`, `git diff --check`, verificação de que staging não escreve direto no catálogo.

## 11. Fora de escopo

- Crawl nacional, cobertura completa de fontes web, OCR/extração adaptativa e publicação automática.
- Agente autônomo de Discovery, elegibilidade ou contato com promotor.
- Escrita obrigatória para todo desafio, submissão automática, CRM/marketplace ou objeto storage.
- Neo4j, Graph Builder, GraphRAG e plataforma de dados.
