# Spec — Knowledge Graph do ecossistema de inovação v2

**Status:** aprovada para implementação · **Data:** 2026-08-05  
**Documento-pai:** [`product-strategy-ecosystem-pathways.md`](product-strategy-ecosystem-pathways.md)  
**Escopo:** requisitos, ontologia, ingestão, matching, workflows, RAG, memória,
avaliação e operação da nova versão do Radar

## 1. Objetivo

Evoluir o Radar para uma plataforma de inteligência e execução que ajude
empresas brasileiras de base tecnológica a encontrar, avaliar e executar
caminhos de inovação.

O Knowledge Graph representa o ecossistema, seus programas, oportunidades,
regras, capacidades tecnológicas e relações temporais. Ele deve permitir que o
sistema responda não apenas “qual oportunidade combina com esta empresa?”, mas:

> “Qual caminho viável conecta esta empresa, seu projeto, as regras aplicáveis,
> as capacidades necessárias e o próximo passo de execução?”

O banco de grafos substituirá o gold relacional como fonte canônica das
entidades, relações e regras do Knowledge Graph. A infraestrutura existente de
ingestão, documentos, RAG, agentes, memória, avaliações e operação deve ser
reaproveitada quando continuar adequada.

## 2. Princípios

1. **Norma separada de fato:** regras de uma oportunidade e fatos do perfil da
   empresa nunca devem ser confundidos.
2. **Instituição, programa e instância separados:** uma instituição mantém um
   programa; uma oportunidade concreta é uma instância temporal desse programa.
3. **Relações têm semântica própria:** credenciamento formal não é o mesmo que
   competência declarada ou histórico de projeto.
4. **Desconhecido não é inelegível:** informação ausente gera estado pendente,
   alerta e próximo passo, nunca eliminação automática.
5. **Regra dura e julgamento subjetivo separados:** regras físicas podem
   eliminar; critérios subjetivos apenas influenciam aderência e confiança.
6. **Proveniência em cada afirmação relevante:** o grafo deve explicar de onde
   veio um nó, uma propriedade ou uma relação.
7. **Humano decide nos pontos de risco:** a ingestão automática é permitida para
   campos estruturados e validáveis; regras complexas permanecem em revisão.
8. **O grafo é canônico; documentos continuam existindo:** o KG não substitui
   documentos-fonte, chunks, artefatos ou histórico de ingestão.
9. **Pré-beta sem overengineering:** avaliação representativa e smoke tests são
   suficientes para validar o produto; não se exige uma plataforma perfeita
   antes do uso controlado.
10. **Investidores privados estão fora do produto ativo.**

## 3. Escopo de produto

O sistema contempla:

- financiamento e apoio público;
- crédito para inovação;
- desafios corporativos e inovação aberta;
- aceleradoras e incubadoras;
- ICTs, laboratórios e infraestrutura tecnológica;
- projetos e intenções de inovação das empresas;
- artefatos de candidatura, proposta, projeto ou resposta a desafio.

Estão fora do escopo ativo:

- venture capital;
- investidores-anjo;
- family offices;
- teses de fundos;
- fundraising privado;
- matching empresa–investidor.

## 4. Requisitos funcionais

### 4.1 Descoberta

O sistema deve descobrir e atualizar entidades, documentos e oportunidades por:

- scrapers determinísticos para fontes conhecidas;
- adapters e catálogos curados;
- Deep Research para fontes desestruturadas ou desconhecidas;
- fontes de infraestrutura, incluindo PNIPE;
- revisão humana para conteúdo de alto risco.

Toda descoberta percorre staging, evidência e promoção antes de se tornar
conhecimento ativo, com exceção de campos de baixo risco explicitamente
validados por regras determinísticas.

### 4.2 Exploração

O usuário pode entrar com uma intenção, problema ou capacidade sem ter um
projeto definido. O sistema deve recuperar possibilidades, capacidades e
caminhos relacionados e ajudar a formar um brief de projeto revisável.

### 4.3 Matching

O sistema deve:

- recuperar candidatos ativos e temporalmente válidos;
- aplicar filtros duros por árvore de critérios;
- avaliar critérios subjetivos sem corte booleano;
- representar `elegível`, `inelegível` e
  `potencialmente_elegível_pendente_informação`;
- explicar evidências, lacunas, incertezas e próximo passo;
- sugerir ICT/parceiro quando obrigatório ou opcionalmente recomendado;
- evitar score universal entre domínios.

### 4.4 Escrita

O assistente de escrita permanece baseado na infraestrutura atual de Writing
Session, chunks, RAG, biblioteca e agentes.

O artefato deve ser tipado conforme o caminho:

- proposta técnica;
- plano de trabalho;
- projeto financiável;
- resposta a desafio;
- application de aceleradora/incubadora;
- conceito de parceria ICT;
- deck ou carta de intenção.

O RAG da escrita combina documentos do caminho, requisitos estruturados,
contexto do projeto e materiais autorizados da empresa.

## 5. Ontologia

### 5.1 Camadas ontológicas

```text
Gênero institucional
  → espécie programática
      → instância concreta e temporal
          → critérios, documentos, requisitos e caminhos
```

### 5.2 Entidades principais

#### `InstituicaoEcossistema`

Superclasse para organizações que participam do ecossistema. Pode assumir
papéis de fomentadora, promotora, operadora, ICT ou provedora de infraestrutura.

Propriedades mínimas:

- `id`;
- `nome`;
- `cnpj` quando disponível;
- `tipos` ou papéis;
- `url_oficial`;
- `localizacao`;
- `status`;
- `source_refs`;
- `valid_from`;
- `valid_until`.

#### `InstituicaoFomentadora`

Entidade macro responsável por manter linhas ou instrumentos de fomento.

Exemplos: FINEP, BNDES, FAPESP, FAPESC e demais FAPs.

#### `InstituicaoPromotora`

Organização que promove desafio, programa de inovação aberta ou processo de
seleção. Pode ser uma empresa, órgão público ou entidade parceira.

#### `LinhaFomento` / `CanalInovacao`

Programa ou canal durável que possui regras gerais e se repete ao longo do
tempo.

Exemplos: Inovacred, PIPE Fase 1, Inova PAPPE, programa de aceleração ou
programa de incubação.

#### `OportunidadeConcreta` / `Edital`

Instância concreta de um programa ou canal, com validade, orçamento, documentos
e canal de acesso próprios.

Exemplos: Edital Inovacred 2026, chamada PIPE vigente ou ciclo de um desafio
corporativo.

#### `EmpresaCliente`

Organização usuária do sistema. Contém identidade e dados relativamente
estáveis da empresa, sem substituir o perfil factual de elegibilidade.

#### `PerfilElegibilidade`

Retrato factual atual da empresa, separado da entidade empresarial.

Para o MVP, apenas o perfil atual é consumido pelo pathfinding:

```text
(EmpresaCliente)-[:PERFIL_ATUAL]->(PerfilElegibilidade)
```

O modelo permite histórico futuro por substituição do relacionamento por
`PERFIL_PASSADO`, sem alterar consultas que buscam `PERFIL_ATUAL`.

Propriedades mínimas:

- `data_atualizacao`;
- `receita_anual_ultimo_exercicio`;
- `cnae_principal`;
- `cnaes_secundarios`;
- `localizacao_uf`;
- `localizacao_regiao`;
- `trl_atual`;
- `porte`;
- `tipo_societario`;
- `source_refs` e evidências.

#### `ProjetoInovacao`

Descrição do problema, solução, tecnologia, objetivo, estágio, orçamento,
cronograma e necessidades de parceria da empresa.

#### `InfraestruturaTecnologica`

Capacidade de desenvolvimento, teste, pesquisa, mentoria ou execução. Inclui,
com propriedade `tipo`:

- `ict`;
- `laboratorio`;
- `aceleradora`;
- `incubadora`;
- `polo_pesquisa`;
- outros tipos futuros controlados.

O “caminho de desenvolvimento” é uma relação envolvendo essa infraestrutura,
um projeto e um canal; não é uma propriedade implícita da entidade.

#### `AreaTecnologica`

Vocabulário controlado de áreas, tecnologias, setores e competências. Deve
suportar hierarquia e aliases, sem permitir que cada fonte crie livremente uma
taxonomia incompatível.

#### `CriterioGrupo`

Nó intermediário de composição lógica com operador `AND`, `OR` ou `NOT`.

#### `CriterioRigido`

Regra verificável por comparação, lista, faixa ou correspondência estruturada.

Tipos iniciais:

- `CNAE`;
- `ReceitaMin`;
- `ReceitaMax`;
- `TRLMin`;
- `TRLMax`;
- `UFPermitida`;
- `RegiaoPermitida`;
- `TipoEmpresa`;
- `Contrapartida`;
- `ExigeICT`;
- `CredenciamentoObrigatorio`;
- `Prazo`;
- `DespesaElegivel`.

#### `CriterioSubjetivo`

Regra qualitativa que exige interpretação, como impacto ambiental, relevância
estratégica ou potencial de transformação. Nunca gera eliminação booleana.

#### `DocumentoFonte` e `Evidencia`

Representam o documento original e o trecho/afirmação que suporta uma
propriedade ou relação do grafo.

## 6. Relações principais

```text
(OportunidadeConcreta)-[:INSTANCIA_DE]->(LinhaFomento)
(LinhaFomento)-[:MANTIDA_POR]->(InstituicaoFomentadora)
(InstituicaoPromotora)-[:PROMOVE]->(CanalInovacao)
(CanalInovacao)-[:TEM_INSTANCIA]->(OportunidadeConcreta)

(EmpresaCliente)-[:PERFIL_ATUAL]->(PerfilElegibilidade)
(EmpresaCliente)-[:POSSUI_PROJETO]->(ProjetoInovacao)
(EmpresaCliente)-[:NECESSITA_DESENVOLVER]->(AreaTecnologica)
(ProjetoInovacao)-[:REQUER_CAPACIDADE]->(AreaTecnologica)

(OportunidadeConcreta)-[:EXIGE]->(CriterioGrupo)
(CriterioGrupo)-[:REQUISITO]->(CriterioRigido|CriterioSubjetivo|CriterioGrupo)
(OportunidadeConcreta)-[:SUPORTA]->(AreaTecnologica)
(InfraestruturaTecnologica)-[:DECLARA_COMPETENCIA]->(AreaTecnologica)
(InfraestruturaTecnologica)-[:EXECUTOU_PROJETO]->(AreaTecnologica)
(InfraestruturaTecnologica)-[:CREDENCIADO_POR]->(InstituicaoFomentadora)

(OportunidadeConcreta)-[:DOCUMENTADA_POR]->(DocumentoFonte)
(Evidencia)-[:SUPORTA]->(entidade_ou_relacao)
(Evidencia)-[:EXTRAIDA_DE]->(DocumentoFonte)
```

`CREDENCIADO_POR` significa exclusivamente credenciamento formal e jurídico.
Competência declarada, histórico de projeto e inferência de Deep Research usam
relações distintas.

## 7. Regras de elegibilidade

### 7.1 Árvore de decisão

Critérios compostos são representados por nós, não por arrays de lógica
embutidos em propriedades:

```text
(Edital)-[:EXIGE]->(grupo: CriterioGrupo {operador: "AND"})
(grupo)-[:REQUISITO]->(CriterioRigido {tipo: "CNAE", valores: ["26", "62"]})
(grupo)-[:REQUISITO]->(CriterioRigido {tipo: "ReceitaMax", valor: 10000000})
```

O avaliador percorre a árvore recursivamente e produz um resultado estruturado
por critério.

### 7.2 Estados do match

```text
elegivel
inelegivel
potencialmente_elegivel_pendente_informacao
inconclusivo_por_conflito
```

`potencialmente_elegivel_pendente_informacao` não aparece como sinal verde. O
resultado deve trazer alerta crítico e próximo passo explícito.

### 7.3 Critérios subjetivos

O avaliador determinístico calcula as regras físicas. A LLM analisa critérios
subjetivos e retorna:

- `confidence`: alta, média ou baixa;
- justificativa;
- evidências utilizadas;
- riscos e perguntas pendentes.

Essa avaliação pode alterar a priorização, mas nunca transformar um critério
subjetivo em bloqueador booleano.

### 7.4 ICT obrigatória ou opcional

Se uma oportunidade exigir formalmente uma ICT ou credenciamento, a ausência do
arranjo necessário vira lacuna crítica do caminho.

Se a oportunidade permitir execução interna, ICTs e laboratórios podem aparecer
como arranjo opcional/recomendado para reduzir risco tecnológico, sem bloquear o
acesso.

## 8. Estados, temporalidade e confiança

Entidades, propriedades e relações relevantes usam:

```text
draft → reviewed → active → superseded
                    ↘ rejected
```

Campos mínimos de governança:

- `status`;
- `source_refs`;
- `confidence`;
- `valid_from`;
- `valid_until`;
- `created_at`;
- `updated_at`;
- `reviewed_by` quando houver revisão humana;
- `producer`;
- `derivation`.

Informação de Deep Research pode estar ativa como sinal secundário quando
explicitamente marcada como `source=deep_research`, com confiança e redação que
indiquem incerteza. Ela não deve ser apresentada como credenciamento ou fato
formal sem fonte oficial.

## 9. Ingestão em duas vias

### 9.1 Via automática

Campos de baixo risco, alta estruturação e validação imediata podem ser
publicados automaticamente:

- datas válidas;
- links oficiais;
- orçamento;
- valores numéricos de corte;
- identificadores e metadados de fonte.

O código valida tipos, faixas, consistência temporal e fonte antes de criar ou
atualizar o nó.

### 9.2 Via de revisão obrigatória

Campos de alto risco entram como `draft`:

- árvores compostas de CNAE;
- exceções e negações;
- contrapartida financeira;
- restrições regionais;
- obrigação de ICT;
- credenciamento formal;
- interpretações jurídicas ou subjetivas.

O LLM gera `DraftNodes` e `DraftRelations`. Um operador aprova, corrige ou
rejeita antes do status `active`.

## 10. Deep Research

### Fase 1 — Descobridor de fontes

Pesquisa links, páginas, PDFs, atas, laboratórios e fontes dispersas. O resultado
é documento bruto ou referência de documento em staging.

### Fase 2 — Propositor consultivo

Sugere entidades e relações, principalmente capacidades de ICTs, laboratórios,
competências e equipamentos.

```text
fonte → DocumentoFonte → proposta de entidade/relação → revisão ou sinal secundário
```

Deep Research nunca escreve diretamente uma verdade canônica no grafo de
produção.

## 11. Seleção do banco de grafos

A escolha do banco será feita por avaliação técnica curta, orientada ao uso
real, considerando:

1. suporte e maturidade do cliente Python;
2. busca vetorial nativa ou integração simples;
3. busca textual e filtros estruturados;
4. transações e consistência;
5. integração com LangGraph e ferramentas Python;
6. custo, licença e risco de lock-in;
7. operação local e produção;
8. backup, observabilidade e recuperação;
9. suporte à temporalidade, proveniência e relações profundas;
10. facilidade de migração do gold relacional existente.

Candidatos iniciais podem incluir Neo4j, Memgraph, ArangoDB, Apache AGE e outras
alternativas tecnicamente compatíveis. A avaliação deve usar um pequeno corpus
representativo e consultas reais:

- caminho empresa → projeto → tecnologia → ICT → oportunidade;
- avaliação recursiva de critérios;
- consulta temporal de oportunidades ativas;
- recuperação textual e vetorial;
- escrita transacional de entidade, relação e evidência;
- atualização e supersessão de fatos.

O banco escolhido substituirá o gold relacional como fonte canônica do KG. O
PostgreSQL existente pode continuar sendo usado para usuários, workspaces,
checkpoints, jobs, documentos, sessões e demais dados transacionais que não são
o grafo.

## 12. Workflows LangGraph

O sistema possui um router leve e três grafos principais:

```text
RadarGraph
├── DiscoveryGraph
├── ExplorationGraph
├── MatchGraph
└── WritingGraph
```

O router não precisa ser LLM. Regras determinísticas roteiam comandos e estados
claros; uma LLM só resolve intenções ambíguas.

### 12.1 DiscoveryGraph

```text
definir escopo
→ escolher coletor
→ scraper/Deep Research/fonte curada
→ extrair evidências
→ normalizar entidade
→ classificar domínio
→ validar
→ revisão humana quando necessário
→ publicar no KG
```

Operações externas assíncronas criam uma execução persistida em estado
`waiting` e retomam o grafo quando o resultado chega.

### 12.2 ExplorationGraph

```text
perfil + intenção
→ recuperar possibilidades e capacidades
→ sintetizar mapa
→ esclarecer intenção
→ formar brief de projeto
```

### 12.3 MatchGraph

```text
carregar perfil/projeto
→ recuperar candidatos no KG
→ filtrar vigência e status
→ Stage 0: validade temporal
→ Stage 1: árvore de elegibilidade
→ Stage 2: aderência semântica
→ Stage 3: avaliação subjetiva/rerank opcional
→ explicar lacunas e próximo passo
→ usuário seleciona caminho
```

### 12.4 WritingGraph

```text
caminho + tipo de artefato
→ recuperar documentos e contexto da empresa
→ construir outline
→ gerar seção
→ criticar
→ revisão humana
→ revisar ou salvar
```

## 13. Estado mínimo dos workflows

```python
class RadarState(TypedDict, total=False):
    run_id: str
    workspace_id: str
    user_id: str
    workflow: str

    user_intent: str
    company_id: str
    profile_id: str
    project_id: str
    pathway_id: str
    artifact_type: str

    source_refs: list[str]
    evidence_refs: list[str]
    candidate_ids: list[str]
    match_results: list[dict]
    eligibility_gaps: list[dict]
    retrieved_chunk_ids: list[str]

    draft_nodes: list[dict]
    draft_relations: list[dict]
    human_review: dict
    errors: list[dict]
    status: str
```

O estado do grafo carrega referências e resultados compactos. Documentos, fontes,
chunks, artefatos e históricos permanecem nos respectivos stores.

## 14. RAG e memória

### 14.1 RAG

O RAG existente deve ser reaproveitado e separado em três usos:

1. **Discovery/contextual:** recuperar documentos para validar e explicar
   entidades e relações.
2. **Match/explainability:** recuperar trechos que sustentam requisitos,
   critérios, confiança e lacunas.
3. **Writing:** recuperar documentos do caminho, requisitos, contexto do projeto,
   perfil e biblioteca da empresa para construir o artefato.

O KG fornece fatos e relações estruturadas; o RAG fornece documentos e contexto.

### 14.2 Memória

Reaproveitar a infraestrutura de memória existente e separar suas funções:

- **procedural:** prompts, playbooks, regras de workflow e ferramentas;
- **semântica:** entidades, relações, áreas tecnológicas e fatos do KG;
- **episódica:** sessões, decisões, revisões e artefatos do workspace;
- **longo prazo:** padrões agregados e aprendizados autorizados entre sessões.

Memória não deve substituir proveniência nem introduzir fatos no KG sem o fluxo
de ingestão correspondente.

## 15. Avaliação

A avaliação deve ser representativa e proporcional ao pré-beta.

### Gates mínimos

- schema e constraints do banco de grafos;
- ingestão de fixture pequena;
- consulta de pathfinding principal;
- avaliação da árvore `AND`/`OR`;
- comportamento `pendente de informação`;
- exclusão de investidores;
- proveniência e estados `draft/active/superseded`;
- smoke test de cada workflow LangGraph;
- recuperação RAG de fonte e contexto da empresa.

### Avaliações diagnósticas

- comparação Deep Research versus scraper;
- qualidade de classificação de domínio;
- precisão de relações ICT/capacidade;
- qualidade de explicações subjetivas;
- latência e custo de consultas;
- qualidade dos artefatos escritos.

Não criar harnesses paralelos. Reaproveitar o harness existente e adicionar
fixtures pequenas e representativas.

## 16. Operação e migração

### Fases

1. avaliar bancos candidatos com corpus e consultas representativas;
2. definir schema físico, constraints, índices e estratégia de backup;
3. implementar adapter/repository canônico do KG;
4. importar entidades, relações e evidências do gold atual;
5. validar pathfinding, critérios e proveniência;
6. trocar leitores de catálogo, Explore e match para o banco de grafos;
7. retirar gold relacional como fonte canônica do KG;
8. manter PostgreSQL para os demais domínios transacionais.

O histórico bruto e os documentos não são apagados. A migração deve ser
reexecutável, versionada e observável.

### Operação mínima

- backup e restauração testados;
- migrations/schema versionados;
- health check do banco;
- métricas de queries e latência;
- logs de ingestão e revisão;
- dead-letter para falhas de ingestão;
- rollback dos leitores antes de remover o gold antigo.

## 17. Critérios de aceite

1. O banco de grafos escolhido atende aos critérios técnicos e operacionais
   documentados.
2. A hierarquia instituição → linha/canal → oportunidade concreta funciona para
   financiamento e programas correlatos.
3. O perfil atual da empresa é consultável por `PERFIL_ATUAL`.
4. Regras compostas são representadas e avaliadas recursivamente.
5. Desconhecidos geram estado pendente e próximo passo, sem sinal verde falso.
6. Critérios subjetivos influenciam ranking, mas nunca aplicam corte hard.
7. Credenciamento formal é separado de competência declarada e histórico.
8. Deep Research produz fontes e propostas rastreáveis, nunca fatos canônicos
   não revisados de alto risco.
9. Discovery, Explore, Match e Writing rodam como grafos persistidos,
   recuperáveis e observáveis.
10. KG, RAG e memória têm fronteiras claras e reutilizam infraestrutura existente
    quando adequada.
11. O fluxo de escrita continua funcionando para oportunidades que exigem
    proposta ou artefato equivalente.
12. Investidores não aparecem nas superfícies ativas.

## 18. Fora de escopo desta implementação

- histórico completo de perfis de empresa;
- inferência jurídica autônoma;
- credenciamento inferido por similaridade;
- completude nacional de ICTs;
- recomendação de investidores;
- automação de submissão ou negociação;
- adoção de banco de grafos sem avaliação comparativa;
- reconstrução de todos os harnesses e avaliações;
- troca obrigatória de todos os componentes existentes quando um adapter
  compatível for suficiente.

