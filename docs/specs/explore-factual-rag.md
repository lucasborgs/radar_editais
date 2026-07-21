# Spec - Grounding factual e RAG no Explorar

**Status:** implementada em pré-produção local; promoção/gate E2E final pendentes - **Data:** 2026-07-15  
**Documento-pai:** [`system-coherence.md`](system-coherence.md)  
**Perfis afetados:** visitante do Explorar, operador e mantenedor do pipeline  
**Impacto:** alto; autoridade documental, ferramentas do agente e avaliacao

## 1. Problema comprovado

Perguntas factuais sobre um edital especifico chegam ao `ExploreAgent`, mas o
agente nao possui uma ferramenta de recuperacao dos documentos normativos. Sua
unica leitura detalhada e `get_edital`, que entrega uma ficha gold resumida:
objetivo, mecanismo, elegiveis, valor e ate cinco `key_requirements`.

Isso cria uma falsa aparencia de RAG. O texto completo existe no Silver e pode
existir em `edital_chunks`/`match_chunks`, mas nao e consultado pelo Explorar.
A resposta, portanto, tende a parafrasear a descricao da pagina da chamada ou
um subconjunto lossy dos requisitos, mesmo quando a pergunta pede uma secao
normativa especifica.

Ha uma segunda classe de risco: documentos rerratificados podem coexistir com
versoes revogadas no Documento Canonico e nos indices. Recuperar chunks sem
resolver autoridade temporal pode produzir uma resposta mais completa, porem
juridicamente desatualizada.

Perguntas sobre entidades estruturadas, como investidores, possuem um modo de
falha adicional: o guard conversacional de `/explorer` pode recusar uma pergunta
que pertence explicitamente ao seu escopo, e as tools atuais nao oferecem uma
ficha detalhada de investidor por ID/nome.

## 2. Evidencias de regressao

### 2.1 FINEP Conhecimento Brasil (`finep:745`)

Query: `Quais os itens financiaveis pelo edital?`

- A resposta do Explorar retornou os objetivos da chamada e a faixa de valor,
  sem qualquer rubrica financiavel.
- O Silver contem a secao `4.3 Itens Financiaveis` e as tabelas correspondentes.
- O corpus local contem ao mesmo tempo o regulamento original de 07/08/2024, a
  rerratificacao de fevereiro de 2025 e a 3a rerratificacao de 09/02/2026.
- A versao original permitia pessoal, diarias/locomocao e servicos PJ com
  recursos Finep, e tambem obras/equipamentos na contrapartida. A 3a
  rerratificacao restringiu ambos os lados a pagamento de pessoal.
- A heuristica de `src/radar/pipeline/adapters/finep.py::_version_info` somente agrupa
  documentos com o token `edital`; nomes reais baseados em `Regulamento` passam
  como nao versionados e coexistem no corpus.

### 2.2 FAPESC 031/2026 (`fapesc:31-2026`)

Query: `Quais os criterios de admissibilidade do edital?`

- A resposta do Explorar listou quatro condicoes verdadeiras, mas omitiu a
  maior parte dos criterios eliminatorios.
- O PDF e o Silver contem integralmente a Secao 4, organizada por empresa,
  proponente/coordenador geral, equipe tecnica e proposta de projeto.
- Entre as omissoes materiais estao sede em SC/JUCESC/CNPJ com ao menos um ano,
  faturamento de ate R$ 1,2 milhao, contrapartida de 5%, vedacao a EI/MEI,
  ausencia de fomento anterior no Acelera, restricoes a projetos em execucao,
  requisitos do coordenador/equipe, TRL >= 2, valor de ate R$ 80 mil, prazo de
  ate 12 meses e video de um minuto.
- Nao ha conflito de versoes evidente neste caso. Ele demonstra que a ausencia
  de RAG factual e suficiente para causar a resposta incompleta.
- A perda e acumulativa: `constraints_producer` comprime toda a elegibilidade
  em no maximo seis frases de display, `get_edital` expoe no maximo cinco e o
  agente pode resumir novamente esse subconjunto. Neste edital, a secao de
  admissibilidade tem cerca de 16,5 mil caracteres, mas o produtor envia apenas
  os primeiros 12 mil ao modelo, descartando ainda parte do texto antes da
  compressao.

### 2.3 Barn Invest (`investidor:barn-invest`)

Queries:

- `Em quais verticais a Barn Invest realiza investimentos?`
- `Qual a tese de investimentos da Barn?`

Em ambas, o Explorar respondeu apenas com o redirect de modo para `/escrita`,
embora perguntas sobre investidores estejam declaradas dentro do escopo de
`/explorer`.

O problema ocorre antes da consulta ao catalogo:

- `workspace_service` anexa o bloco de modo ao conteudo da mensagem do usuario,
  embora o comentario diga que ele seria injetado no system prompt;
- o bloco ordena ao modelo responder `APENAS` com um texto fixo quando ele
  classificar a pergunta como fora do escopo;
- o modelo confundiu o substantivo `investimentos` com uma intencao de escrever
  ou refinar proposta e emitiu um falso redirect;
- nao ha teste de regressao para perguntas factuais sobre investidores dentro
  do workspace multi-modo.

Mesmo removido o redirect incorreto, a ferramenta `list_investidores` retorna
somente nome e ate tres temas. O catalogo possui uma tese curta, setores,
keywords, estagio e URL oficial da Barn, e `entity_catalog.get_investidor` ja
reconstroi a ficha, mas essa leitura por investidor nao esta exposta ao
`ExploreAgent`.

A fonte oficial lista quatro verticais: agro e uso da terra; transporte e
mobilidade; industria limpa e economia circular; energia renovavel e eficiencia
energetica. O registro curado resume corretamente as quatro familias em
`setores` e na tese, mas nao preserva todos os subtemas exibidos no site.

## 3. Diagnostico

As falhas devem ser tratadas separadamente:

1. **Autoridade documental:** decidir qual documento e versao governam cada
   familia normativa antes do chunking.
2. **Grounding factual do Explorar:** recuperar trechos do edital selecionado
   para perguntas cuja resposta nao cabe na ficha gold.
3. **Grounding de entidades estruturadas:** consultar a ficha exata de um
   investidor/programa/ICT nomeado, sem depender de listagem ou busca truncada.
4. **Roteamento de modo:** impedir que o guard conversacional recuse consultas
   que pertencem ao escopo declarado do modo atual.

Nao se trata inicialmente de tuning de embedding, reranker ou modelo. Nos casos
observados, a recuperacao documental sequer ocorreu.

## 4. Resultado pretendido

Quando houver um edital em foco e a pergunta pedir informacao normativa ou
exaustiva, o Explorar deve:

1. identificar a intencao factual (por exemplo: itens financiaveis,
   admissibilidade, elegibilidade, documentos, contrapartida, prazos ou
   criterios de avaliacao);
2. consultar chunks somente do edital em foco e somente de documentos vigentes;
3. recuperar todas as subsecoes necessarias, sem limitar a resposta aos cinco
   `key_requirements` do card;
4. distinguir requisitos obrigatorios de preferencias e criterios de merito;
5. citar documento, versao/data, secao e pagina;
6. declarar lacuna ou conflito quando a autoridade nao puder ser determinada;
7. nunca apresentar documento analogo como regra do edital consultado.

A ficha gold continua adequada para descoberta e cards. Ela nao deve ser
tratada como substituta do documento em perguntas factuais detalhadas.

Quando a pergunta nomear uma entidade do ecossistema, o agente deve resolver o
ID/nome, ler sua ficha estruturada e responder com os campos sustentados pelo
catalogo, indicando a fonte e a data de verificacao. A presenca de palavras como
`investimento`, `tese` ou `portfolio` nunca deve acionar redirect para escrita.

## 5. Sequencia proposta de implementacao

### 5.1 Contrato unico de roteamento

O roteamento sera hibrido: regras deterministicas governam contexto, escopo e
execucao; uma classificacao LLM fechada participa somente quando a intencao
permanecer ambigua.

Entrada normalizada (`RouteContext`):

- `mode`: `explorer | plan | escrita`;
- `target_type`: `edital | investidor | programa | ict | none`;
- `target_id`: identificador resolvido a partir da tela, URL, ID ou nome;
- `message`: texto original, sem blocos internos anexados;
- `has_profile`, `has_documents` e `workspace_id`, apenas como sinais de
  disponibilidade, sem ampliar permissoes.

Saida fechada (`RouteDecision`):

- `intent`: `EDITAL_SUMMARY | EDITAL_FACT | EDITAL_FACT_ENUMERATIVE |
  ENTITY_FACT | DISCOVERY | MATCH_PROFILE | CONCEPTUAL | PLAN_ACTION |
  WRITING_ACTION`;
- `target_type`, `target_id` e `facets`;
- `confidence` e `reason_code` observaveis;
- `retrieval_profile`: perfil deterministico escolhido depois da classificacao.

Precedencia:

1. comando explicito (`/explorer`, `/plan`, `/escrita`);
2. entidade em foco na UI ou ID/URL explicito na mensagem;
3. regras de alta precisao para intencoes factuais e de acao;
4. classificador LLM com JSON schema, temperatura zero e sem tools;
5. fallback seguro para exploracao ou pedido de esclarecimento.

O classificador nao pode selecionar documento vigente, executar tool, emitir
redirect ou responder ao usuario. A politica deterministica converte a decisao
em tools permitidas. Redirect exige verbo de acao explicito sobre plano ou
rascunho; termos como `investimento`, `tese`, `portfolio` e `verticais` sao
sempre factuais no `/explorer`.

### 5.2 Perfis de recuperacao

| Perfil | Uso | Contrato |
|---|---|---|
| `gold_summary` | resumo de edital/card | ficha gold, sem promessa de exaustividade |
| `entity_detail` | entidade nomeada | ficha estruturada completa e proveniencia |
| `factual_point` | fato pontual de edital | somente edital alvo e docs vigentes; hybrid top-k |
| `factual_enumerative` | listas/secoes completas | busca da secao + expansao de subsecoes irmas por cobertura |
| `discovery_semantic` | descoberta/ecossistema | busca semantica, HyDE/rerank quando aprovados por eval |
| `match` | afinidade com perfil | funil match v3; nao responde fato normativo |

No retrieval documental, manter duas queries diferentes:

- `raw_query`: BM25/FTS, flags de metadata, reranker, logs e exibicao;
- `dense_query`: pseudo-documento HyDE quando habilitado, usado somente para o
  embedding do braco dense.

O comportamento atual sobrescreve `query` com a saida HyDE, fazendo detalhes
hipoteticos contaminarem BM25, flags e rerank. A refatoracao deve separar os
dois sinais e cobri-los por teste. Para `factual_enumerative`, HyDE fica
desabilitado inicialmente; headings e cobertura estrutural prevalecem sobre o
top-k puramente relevancia.

### 5.3 Pacotes de entrega e dependencias

#### PR 0 - baseline reproduzivel

- Adicionar fixtures hermeticas dos tres casos sem chamadas externas.
- Cobrir route decision, tools disponiveis, corpus selecionado e resposta
  esperada em camadas separadas.
- Registrar os casos no harness unificado: ampliar `rag` para retrieval e criar
  uma Suite `explore` no mesmo `radar.core.eval` para roteamento/grounding end-to-end.
- Nenhuma mudanca de comportamento neste pacote.

#### PR 1 - roteamento e entidades (corrige Barn)

- Criar um modulo puro de politica de intencao para o workspace/ExploreAgent.
- Remover `mode_redirect_block` da mensagem do usuario; manter redirect somente
  para `PLAN_ACTION`/`WRITING_ACTION` confirmados.
- Expor `get_entity` tipada ou `get_investidor` reutilizando
  `entity_catalog.get_investidor`.
- Adicionar `route_decision`, `target_id` e tools chamadas aos metadados do
  turno, sem armazenar raciocinio livre do modelo.
- Gate: as duas queries Barn resolvem `ENTITY_FACT` e nunca retornam redirect.

#### PR 2 - autoridade e frescor documental (pre-requisito do RAG factual)

- Editar primeiro `wikis/finep.md`/`wikis/fapesc.md`, conforme autoridade das
  regras por fonte.
- Estender `CanonicalDocEntry` com metadata opcional de familia, revisao, data,
  URL e estado `vigente | superseded`, preservando compatibilidade do JSONB
  existente; migration SQL so sera criada se o contrato exigir coluna fora do
  JSONB.
- Fazer `canonical_hash` e `_source_hash` incluirem os metadados de autoridade.
- Propagar autoridade aos blocos Silver e a `edital_chunks.metadata`.
- Corrigir selecao de `Regulamento`/`Anexo` da FINEP e composicao de
  retificacoes/erratas da FAPESC.
- Reordenar o ETL para `scrape -> adapter/autoridade -> source_docs.save ->
  Silver -> gold -> chunks`, eliminando leitura durable-first de uma versao
  anterior na mesma run.
- Gate: o corpus ativo de `finep:745` possui somente a regra vigente; versoes
  historicas permanecem auditaveis, mas fora do indice factual ativo.

#### PR 3 - kernel factual e cobertura estrutural

- Extrair de `writing_tools.search_edital` um servico read-only reutilizavel.
- Separar `raw_query`/`dense_query` no retriever; HyDE afeta somente o dense.
- Implementar perfis `factual_point` e `factual_enumerative`, sempre sem
  analogos no Explore factual.
- Adicionar expansao por `section`/hierarquia para recuperar subsecoes irmas
  depois do hit inicial.
- Registrar tool factual no ExploreAgent e exigir uso conforme `RouteDecision`.
- Gate FINEP: resposta vigente correta com fonte/secoes 4.3 e 4.5.
- Gate FAPESC: cobertura das quatro familias 4.2-4.5, nao apenas um top-k de
  requisitos da empresa.

#### PR 4 - proveniencia e resposta

- Definir um payload de evidencia com `chunk_id`, `edital_id`, documento,
  versao/data, secao, pagina, URL e autoridade.
- Exibir citacoes na resposta e preservar os IDs usados no trace do turno.
- Diferenciar `resumo parcial`, `lista completa segundo a secao` e `conflito de
  autoridade`.
- Abstencao explicita quando nao houver corpus vigente ou evidencia suficiente.

#### PR 5 - backfill, avaliacao e rollout

- Regravar `edital_source_docs`, forcar rebuild Silver, rodar gold `--no-skip`
  para os afetados e reindexar `edital_chunks`.
- Rodar unitarios, `ruff`, suite `rag`, suite `explore` e gates especificos dos
  tres casos.
- Publicar primeiro decisao de rota e traces em shadow; ativar resposta factual
  somente depois de autoridade e retrieval passarem os gates.
- Manter kill switch operacional para o novo caminho factual durante o rollout,
  sem classificar a correcao como capacidade experimental permanente.

Runbook para os casos motivadores, em ambiente com credenciais:

```bash
python -m radar.core.kg.gold --source investidor
python scripts/reindex_all.py --edital-id finep:745 --edital-id fapesc:31-2026 --force
python -m radar.core.kg.gold --source edital --edital-id finep:745 --edital-id fapesc:31-2026
python -m radar.core.eval run rag --publish
EVAL_EXPLORE_CONNECTED=true python -m radar.core.eval run explore --publish
```

Depois do backfill, inspecionar `edital_chunks.metadata` dos dois editais antes
de ligar tráfego: FINEP deve ter apenas a 3ª rerratificação ativa no retrieval;
FAPESC deve preservar a composição base/emendas. O kill switch é
`EXPLORE_FACTUAL_RAG_ENABLED=false`.

Para listas normativas, `EDITAL_FACT_ENUMERATIVE` não usa o loop ReAct: a
política determinística fixa alvo/perfil, o kernel expande a família estrutural
vigente e `factual_synthesis.py` executa uma síntese fechada por subfamília. O
modelo continua responsável pela prosa, mas não escolhe tool, edital ou seções.
O override desse tier é `FACTUAL_SYNTHESIS_MODEL`.

### 5.4 Arquivos inicialmente afetados

| Area | Arquivos principais |
|---|---|
| Regras por fonte | `wikis/finep.md`, `wikis/fapesc.md`, `WIKI.md` se o contrato global mudar |
| Roteamento | novo modulo em `src/radar/core/services/`, `workspace_service.py`, `explore_agent.py` |
| Entidades | `explore_tools.py`, `entity_catalog.py` |
| Autoridade | `src/radar/pipeline/adapters/base.py`, adapters/extractors FINEP e FAPESC, `source_docs.py` |
| Silver/chunks | `structurer.py`, `chunker.py`, `tasks.py` |
| Retrieval | `retriever.py`, nucleo extraido de `writing_tools.py` |
| API/trace | router de workspace/explore e schemas de resposta, se necessario |
| Testes | `test_workspace_service.py`, `test_explore_agent.py`, `test_finep_adapter.py`, `test_retriever.py` e novos testes de rota/autoridade |
| Avaliacao | `src/radar/core/eval/rag.py`, nova Suite `src/radar/core/eval/explore.py`, registry e golden versionado |

### 5.5 Contrato do golden dos casos motivadores

As respostas do NotebookLM fornecidas na investigacao sao a referencia de
conteudo para os tres casos motivadores, com estas regras:

1. o golden e semantico, nao uma comparacao literal de texto ou formatacao;
2. afirmacoes devem ser divididas em `required`, `forbidden` e `conditional`;
3. a evidencia oficial recuperada deve sustentar cada afirmacao material;
4. no caso FINEP, autoridade temporal prevalece sobre a resposta historica do
   NotebookLM: a referencia deve refletir a 3a rerratificacao de 09/02/2026;
5. informacao adicional e aceita somente se estiver sustentada pela evidencia
   do caso e nao contradisser o golden;
6. omissao de qualquer afirmacao `required` reprova o gate de completude;
7. presenca de qualquer afirmacao `forbidden` reprova o gate de correcao,
   mesmo que o restante da resposta esteja correto.

O dataset deve preservar a resposta original do NotebookLM em
`reference_answer`, a justificativa das adaptacoes em `adaptation_notes` e o
contrato avaliavel em campos estruturados. Formato minimo por caso:

```json
{
  "id": "finep-745-itens-financiaveis",
  "query": "Quais os itens financiaveis pelo edital?",
  "target": {"type": "edital", "id": "finep:745"},
  "expected_route": "EDITAL_FACT_ENUMERATIVE",
  "reference_answer": "resposta original preservada",
  "adaptation_notes": ["aplicar a 3a rerratificacao de 09/02/2026"],
  "assertions": {
    "required": [],
    "forbidden": [],
    "conditional": []
  },
  "evidence": []
}
```

Sao quatro casos executaveis, agrupados em tres incidentes:

| Caso | Rota esperada | Obrigatorio | Proibido |
|---|---|---|---|
| FINEP 745 - itens financiaveis | `EDITAL_FACT_ENUMERATIVE` | versao vigente, itens 4.3/4.5 e pagamento de pessoal nos lados aplicaveis | apresentar obras, equipamentos, diarias ou PJ como rubricas vigentes |
| FAPESC 31/2026 - admissibilidade | `EDITAL_FACT_ENUMERATIVE` | quatro categorias da Secao 4, thresholds e vedacoes materiais descritos nos criterios de aceitacao | reduzir a resposta aos quatro requisitos atualmente retornados ou transformar preferencias em obrigacoes |
| Barn - verticais | `ENTITY_FACT` | exatamente as quatro verticais da referencia | redirect para escrita ou vertical inventada |
| Barn - tese | `ENTITY_FACT` | greentech/transicao verde, America Latina e separacao entre tese e verticais | redirect para escrita ou tratar regulacao institucional como vertical |

O gate final nao deve depender apenas de juiz LLM. Rota, target, uso de fonte
vigente, citacoes e afirmacoes proibidas sao verificacoes deterministicas. Um
juiz LLM com rubrica fechada pode avaliar equivalencia semantica e completude
das afirmacoes obrigatorias, sempre retornando evidencias por item.

### 5.6 Ordem de execucao e gates

Cada pacote somente avanca quando seu gate local passa:

1. **PR 0:** versionar o golden acima e reproduzir as falhas atuais;
2. **PR 1:** aprovar os dois casos Barn em roteamento e grounding de entidade;
3. **PR 2:** aprovar autoridade temporal e selecao de corpus da FINEP/FAPESC;
4. **PR 3:** aprovar retrieval/cobertura das secoes, antes de avaliar a prosa;
5. **PR 4:** aprovar as quatro respostas end-to-end contra o golden semantico;
6. **PR 5:** repetir os gates apos backfill e em ambiente de rollout.

Para localizar regressao, cada caso produz resultados em quatro camadas:

- `routing`: intent e target corretos;
- `authority_retrieval`: documentos vigentes e secoes esperadas recuperados;
- `grounding`: afirmacoes da resposta sustentadas pelos chunks citados;
- `answer_quality`: cobertura de `required`, ausencia de `forbidden` e uso
  correto de `conditional`.

O criterio de saida da spec e 4/4 casos aprovados em todas as camadas. Media
agregada nao compensa falha individual em um dos casos motivadores.

As fases abaixo descrevem os mesmos workstreams tecnicos; a ordem executavel e
a dos PRs acima.

### Fase A - autoridade e versoes

1. Documentar em `wikis/finep.md` as familias normativas (`Regulamento`,
   `Anexo 1`, FAQ e equivalentes), a precedencia de rerratificacoes e o uso de
   metadados oficiais de publicacao quando disponiveis.
2. Revisar a regra FAPESC que hoje inclui `retificacao` e `errata` na skip-list:
   emendas normativas nao podem ser descartadas sem antes serem compostas com o
   edital-base ou marcadas por autoridade temporal.
3. Ampliar o contrato do Documento Canonico para preservar metadados de
   autoridade suficientes, ou manter um manifesto associado que indique
   familia, data, versao e estado vigente/superseded.
4. Corrigir a selecao de versoes da FINEP e cobrir filenames reais por testes.
5. Regravar `edital_source_docs`, invalidar o Silver e reindexar gold e
   `edital_chunks` para os editais afetados.

### Fase B - ferramenta factual do Explorar

1. Extrair/reutilizar o nucleo read-only de `search_edital` da WritingSession,
   sem dependencia de sessao de escrita.
2. Registrar no `ExploreAgent` uma ferramenta escopada a um ou poucos
   `edital_ids`, sem expansao para analogos.
3. Retornar trechos com `source_file`, `section`, `page_range`, versao e
   autoridade; permitir leitura completa por ponteiro quando necessario.
4. Tornar a chamada obrigatoria para perguntas factuais detalhadas sobre edital
   especifico. `get_edital` permanece para resumo e descoberta.

### Fase C - resposta e observabilidade

1. Exibir citacoes de proveniencia na resposta e, quando util, links aos
   documentos oficiais.
2. Registrar nos metadados do turno quais chunks e versoes sustentaram a
   resposta.
3. Sinalizar ao usuario quando a resposta for resumo parcial, em vez de usar
   formulacoes que aparentem exaustividade.

### Fase D - entidades e guard de modo

1. Expor uma tool de detalhe por entidade, começando por `get_investidor`, ou
   uma `get_entity` tipada que reutilize as fichas existentes no
   `entity_catalog`.
2. Fazer a tool devolver tese, setores/verticais, estagio, ticket quando houver,
   URL oficial e `verificado_em`, sem truncar silenciosamente a descricao.
3. Remover o bloco de redirect do conteudo da mensagem do usuario. Se a
   restricao continuar necessaria, aplica-la no system prompt ou por um gate de
   intencao de acao, sem bloquear perguntas factuais.
4. Tratar como in-scope no `/explorer` perguntas sobre tese, verticais,
   portfolio, ticket, estagio e setores de investidores.

## 6. Criterios de aceitacao

### Caso FINEP 745

- A query de itens financiaveis cita a 3a rerratificacao de 09/02/2026 e os
  itens 4.3/4.5.
- A resposta vigente nao lista obras, equipamentos, diarias ou PJ como rubricas
  atuais.
- Se o usuario pedir explicitamente a versao de 07/08/2024, a resposta pode
  descrever as rubricas historicas, identificando a versao como superada.

### Caso FAPESC 31-2026

- A query de admissibilidade cobre, no minimo, as quatro categorias da Secao 4:
  empresa, coordenador geral, equipe tecnica (se houver) e proposta.
- Inclui os thresholds materiais: um ano de CNPJ, R$ 1,2 milhao, 5% de
  contrapartida, TRL >= 2, R$ 80 mil e 12 meses.
- Inclui as principais vedacoes: EI/MEI, fomento anterior no Acelera e proposta
  duplicada/similar.
- Distingue criterios preferenciais de obrigatorios e informa que os itens 4 e
  5 sao verificados na analise de admissibilidade.

### Caso Barn Invest

- As duas queries nao retornam redirect de modo.
- A pergunta de verticais retorna exatamente as quatro familias sustentadas
  pela fonte oficial e pelo catalogo.
- A pergunta de tese informa o foco em greentech/transicao verde na America
  Latina e separa tese, verticais e informacoes institucionais.
- A resposta pode mencionar parceria de longo prazo e regulacao pela CVM como
  contexto, mas nao as apresenta como vertical de investimento.
- Afirmacoes mais granulares, como subtemas de cada vertical, exigem que esses
  dados estejam no catalogo ou sejam recuperados da fonte oficial com
  proveniencia.

### Qualidade transversal

- Toda afirmacao normativa material possui proveniencia recuperavel.
- Ausencia de chunks ou autoridade ambigua produz abstencao explicita, nao uma
  resposta baseada apenas no card.
- Os casos entram na suite unificada de avaliacao; nao se cria harness paralelo.

## 7. Fora de escopo desta proposta

- mudar o ranking de afinidade do Radar;
- usar `match_chunks` como substituto direto de `edital_chunks` sem contrato de
  proveniencia e autoridade;
- aumentar indiscriminadamente o tamanho de `requisitos_texto` nos cards; e
- ativar deep research web como fallback silencioso para documentos ja
  presentes no corpus oficial.
