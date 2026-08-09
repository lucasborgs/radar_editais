# Spec de evolução — Ecossistema de conhecimento

**Status:** direcional, não mandatória · **Data:** 2026-08-08
**Spec mandatória:**
[`strategic-consultant-v1.md`](strategic-consultant-v1.md)
**Pesquisa relacionada:**
[`../research/neo4j-llm-graph-builder.md`](../research/neo4j-llm-graph-builder.md)

## 1. Propósito e autoridade

Este documento preserva a direção de evolução do plano de conhecimento, da
extração, do KG, do Match, do RAG, da memória e da operação. Ele não é uma lista
de requisitos para concluir o Consultor estratégico v1.

Cada capacidade descrita aqui é uma hipótese ou destino possível. Ela só entra
na rota crítica quando resolve um problema observado na jornada mandatória e
cumpre seu critério de promoção. A ausência de promoção significa adiar ou
descartar, não manter uma implementação experimental indefinidamente.

## 2. Norte arquitetural

O Radar evolui para uma autoridade conceitual de entidades, relações, fatos e
evidências versionadas:

```text
fontes e documentos
→ afirmações evidenciadas
→ fatos e relações vigentes
→ caminhos de inovação
→ decisões e artefatos
```

Nesse cenário:

- o catálogo é uma projeção reconstruível;
- o consultor navega pelo KG;
- a avaliação de caminhos calcula sobre conhecimento estruturado e evidências;
- a escrita planeja pelo KG e redige com RAG;
- índices textuais e vetoriais são otimizações derivadas e substituíveis.

Esse norte não determina antecipadamente banco, fornecedor ou topologia de
armazenamento.

## 3. Evolução da inteligência documental

### 3.1 Extração adaptativa

A antiga frente de extração adaptativa permanece como prioridade de evolução.
O pipeline começa pela alternativa mais barata capaz de preservar a informação:

```text
texto nativo
→ análise de layout e tabelas quando necessário
→ OCR quando não houver texto confiável
→ visão somente para conteúdo visual não recuperável antes
```

O roteamento deve ser decidido por sinais do próprio documento, como ausência
de texto, densidade incomum, tabelas, páginas digitalizadas, baixa confiança ou
campos críticos não encontrados.

Prioridade de extração:

1. regras e condições;
2. prazos e temporalidade;
3. valores, faixas e contrapartidas;
4. tabelas e anexos normativos;
5. evidências e localizadores precisos;
6. demais conteúdo consultivo.

Saída desejada:

```text
DocumentoFonte
→ conteúdo estruturado
→ Afirmações
→ Evidências localizadas
→ DraftNodes/DraftRelations quando aplicável
```

**Critério de promoção:** ganho material na recuperação de campos críticos ou
na precisão das evidências em documentos que o pipeline atual não resolve, com
custo e latência aceitáveis.

### 3.2 Documento canônico e linhagem

Documentos brutos, canônicos e derivados devem convergir para uma identidade
estável, hash, versão e proveniência. A fundação atual de `SourceBundle`, source
docs, staging e revisão deve ser simplificada e aprofundada, não duplicada.

Evoluções possíveis:

- versionamento explícito de retificações e anexos;
- reprocessamento seletivo por documento e produtor;
- estado independente das projeções KG e RAG;
- object storage durável quando o filesystem se tornar risco real;
- manifests reproduzíveis quando houver múltiplos ambientes ou modelos em uso.

**Critério de promoção:** uma falha observada de rastreabilidade, recuperação,
reprocessamento ou durabilidade que não seja resolvida pelo mecanismo atual.

## 4. Evolução da ontologia

### 4.1 Ontologia mínima primeiro

A ontologia cresce a partir das perguntas e caminhos reais. Ela não precisa
modelar todo o ecossistema antecipadamente.

Núcleo inicial:

- `EmpresaCliente` e `PerfilEmpresa`;
- `BriefProjeto` e `ProjetoInovacao`;
- `CaminhoInovacao`;
- `Oportunidade`;
- `Programa` ou `CanalInovacao` quando necessário;
- `AtorEcossistema` com papéis explícitos;
- `CapacidadeTecnologica`;
- `Afirmação`, `Evidência` e `DocumentoFonte`.

### 4.2 Afirmações governadas

`Afirmação` torna-se um objeto próprio para conteúdo:

- temporal;
- normativo;
- conflitante;
- proveniente de múltiplas fontes;
- crítico para elegibilidade ou escrita.

Modelo conceitual:

```text
fonte declara Afirmação
Afirmação descreve sujeito + predicado + objeto/valor
Evidência sustenta Afirmação
política de confiança escolhe a projeção canônica vigente
```

Relações simples e estáveis não precisam obrigatoriamente virar nós de
afirmação. A reificação é aplicada onde a governança justifica seu custo.

**Critério de promoção:** a projeção atual não consegue representar
temporalidade, conflito ou proveniência de uma decisão importante sem perda de
significado.

### 4.3 Regras e elegibilidade

Regras podem evoluir de propriedades estruturadas para árvores `AND`, `OR` e
`NOT`, com critérios rígidos e subjetivos separados. Isso é especialmente útil
para CNAE, localização, porte, TRL, contrapartida, prazo, despesas e obrigação
de parceria.

Regras rígidas são avaliadas deterministicamente. Critérios subjetivos recebem
interpretação e confiança, mas não eliminam automaticamente um caminho.

**Critério de promoção:** regras reais das verticais não puderem ser explicadas
ou avaliadas corretamente pelo modelo mais simples.

### 4.4 ICTs e arranjos de execução

O conhecimento de ICTs deve evoluir de listas de afinidade para arranjos
explicáveis:

```text
ProjetoInovacao
→ requer CapacidadeTecnologica
→ oferecida por ICT/Laboratório
→ compatível com regra ou programa
→ forma possível arranjo de execução
```

Devem permanecer distintas:

- competência declarada;
- infraestrutura/equipamento disponível;
- histórico comprovado de projeto;
- credenciamento formal;
- afinidade inferida.

**Critério de promoção:** melhoria observável na formação de caminhos e na ação
recomendada, sustentada por evidências suficientes.

## 5. Evolução do Knowledge Graph

### 5.1 Backend substituível

O contrato `Knowledge` da spec mandatória é a fronteira de migração. O gold
relacional, um property graph ou outra representação podem atendê-lo enquanto
preservarem semântica, evidência, temporalidade e performance suficiente.

### 5.2 Neo4j

Neo4j é a preferência para experimentação por oferecer property graph,
consultas de caminho, ecossistema GraphRAG e boa legibilidade para portfólio. A
primeira opção deve ser local ou gratuita e operacionalmente simples.

Neo4j só deve se tornar canônico quando um benchmark demonstrar vantagem
material sobre a implementação atual em consultas reais de produto, sem impor
um custo operacional desproporcional.

Consultas mínimas para comparação:

- empresa/projeto → capacidades → ICT → oportunidade;
- instituição → programa → oportunidade vigente;
- caminho com regras, lacunas e evidências;
- travessia temporal e supersessão de fatos;
- recuperação híbrida por estrutura, texto e vetores.

### 5.3 Neo4j LLM Graph Builder

Papéis possíveis:

- ferramenta de prototipagem;
- acelerador de extração;
- produtor de `DraftNodes` e `DraftRelations`;
- base experimental de GraphRAG integrado.

Ele não é autoridade ontológica nem publica automaticamente fatos críticos. O
resultado passa por resolução de entidades, evidência, confiança e revisão
proporcional ao risco.

**Critério de promoção:** entidades ou relações novas e úteis, com evidência
rastreável, melhorando respostas ou caminhos mais do que uma extração simples.

### 5.4 Property graph, RDF/OWL e schema

Property graph permanece a opção preferencial inicial pela simplicidade de
implementação e navegação. RDF/OWL só deve ser reconsiderado se houver demanda
real de interoperabilidade semântica, raciocínio formal ou publicação de dados
ligados que um property graph não atenda de forma simples.

O schema físico deve seguir a ontologia validada pelo produto; ele não deve
definir o vocabulário por conveniência do banco.

## 6. Chunks, embeddings e GraphRAG

Permanecem três opções legítimas:

1. Neo4j com entidades, relações, documentos, chunks e embeddings;
2. Neo4j para o grafo e vector store separado;
3. arquitetura híbrida com índices derivados por consumidor.

A escolha deve comparar:

- qualidade de recuperação;
- explicabilidade e proveniência;
- latência e custo;
- simplicidade de atualização e reindexação;
- backup e recuperação;
- carga operacional para um desenvolvedor solo.

A opção mais integrada não é automaticamente a mais simples. Até existir
benchmark, os índices atuais podem permanecer como projeções reconstruíveis.

**Critério de promoção:** melhoria mensurável nas verticais de exploração,
avaliação de caminhos ou escrita, com redução ou aumento aceitável de
complexidade.

## 7. Evolução dos workflows

### 7.1 `ConsultantGraph`

Evoluções possíveis depois da jornada mandatória:

- gestão de contexto por poda e resumo sem perder evidências;
- thread persistente por sessão;
- interrupções para revisão de decisões relevantes;
- fan-out de pesquisa ou comparação quando trouxer ganho real;
- memória de padrões autorizados entre projetos;
- observabilidade de custo, latência e decisões do agente.

O LangGraph permanece responsável pela experiência ambígua e iterativa, não por
transformar jobs determinísticos em agentes.

### 7.2 Avaliação de caminhos e Match

As fases atuais do Match são aprendizado, não restrição arquitetural. O módulo
pode evoluir para combinar:

- recuperação sobre o KG;
- filtros temporais e regras duras;
- travessias e relações estruturais;
- afinidade semântica;
- análise subjetiva por LLM;
- cobertura de lacunas e custo do próximo passo.

Descoberta e exploração privilegiam recall; gates de elegibilidade privilegiam
precisão. Nenhum score universal precisa sobreviver entre domínios diferentes.

**Critério de promoção:** mais caminhos relevantes encontrados sem aumento
inaceitável de falsos sinais, com explicações melhores que o fluxo substituído.

### 7.3 Exploração

O consultor pode aprofundar relações, pedir pesquisa adicional e transformar
descobertas em atualizações do brief ou propostas de caminho. `match_chunks` ou
qualquer índice específico permanecem detalhes substituíveis, não contratos do
produto.

### 7.4 Escrita

A escrita evolui para planejar a partir do `CaminhoInovacao`. O KG fornece
estrutura, requisitos, lacunas e decisões; o RAG fornece trechos documentais e
materiais da empresa; a LLM planeja, redige, critica e revisa.

Evoluções possíveis:

- artefatos tipados por caminho;
- outline fundamentado em requisitos;
- rastreabilidade de afirmações do texto até evidências;
- crítica especializada por mecanismo;
- reavaliação do caminho quando a escrita revelar uma lacuna.

**Critério de promoção:** artefatos mais completos e fundamentados, com menos
correções factuais pelo usuário.

## 8. Memória e aprendizado

A memória pode evoluir em quatro categorias:

- **de trabalho:** contexto ativo do `ConsultantGraph`;
- **episódica:** decisões e artefatos de um projeto;
- **semântica:** conhecimento do ecossistema, mantido no KG;
- **procedural:** playbooks, prompts e estratégias autorizadas.

Possibilidades futuras incluem histórico/diff/restauração de perfil, painel “O
Radar lembra”, padrões entre projetos e overlays aprendidos. Nenhuma memória
pode criar fatos do KG ou modificar o perfil sem origem e autorização
adequadas.

**Critério de promoção:** continuidade percebida ou melhor decisão, sem
contaminação entre empresas, projetos ou fontes.

## 9. Operação e plano de dados

Capacidades operacionais futuras podem incluir:

- hashes e versões de pipeline, schema, ontologia e modelos;
- materializações independentes por consumidor;
- reprocessamento seletivo e idempotente;
- retries, dead-letter, backfill e rollback;
- releases reproduzíveis entre teste, staging e produção;
- object storage versionado;
- métricas de freshness, cobertura, custo, latência e revisão.

Elas devem ser adicionadas conforme o número de fontes, ambientes, modelos e
falhas reais justificar. O ledger de jobs, health checks, sanitização e
observabilidade já existentes são fundação reaproveitável.

**Critério de promoção:** risco operacional observado ou crescimento de escala
que torne a solução atual insuficiente.

## 10. Avaliação proporcional

O harness existente continua sendo a autoridade de avaliação. Novos testes
devem ser casos reais ou representativos das duas verticais, não uma tentativa
de preservar todo comportamento legado.

Experimentos prioritários:

1. qualidade ponta a ponta do `CaminhoInovacao`;
2. extração adaptativa de regras, prazos, valores, tabelas e evidências;
3. KG relacional versus Neo4j em consultas reais;
4. Graph Builder versus produtores específicos;
5. recuperação integrada versus vector store separado;
6. planejamento da escrita pelo KG e redação por RAG;
7. continuidade e profundidade do `ConsultantGraph`.

Uma capacidade é promovida quando melhora valor, confiança ou operação em caso
representativo. Deve ser descartada quando apenas duplica sinais existentes ou
acrescenta complexidade sem melhorar a jornada.

## 11. Sequência direcional

Sem constituir um plano de execução, a dependência conceitual é:

```text
jornada mandatória e contratos centrais
→ extração adaptativa nos campos críticos
→ afirmações/evidências onde houver conflito ou temporalidade
→ ontologia ampliada pelas perguntas reais
→ benchmark Neo4j/GraphRAG
→ promoção seletiva de armazenamento e operação
```

Etapas podem avançar em paralelo quando independentes, mas nenhuma mudança de
infraestrutura deve bloquear a entrega da experiência consultiva.

## 12. Decisões adiadas explicitamente

- Neo4j como banco canônico;
- uso produtivo do LLM Graph Builder;
- localização final de documentos, chunks e embeddings;
- GraphRAG unificado;
- RDF/OWL;
- object storage obrigatório;
- releases completos de dados por ambiente;
- memória autônoma entre projetos;
- representação de toda regra como nó;
- cobertura nacional completa de ICTs e oportunidades.

Adiar essas decisões é deliberado. Elas permanecem documentadas para que sejam
avaliadas com evidência, sem consumir antecipadamente a rota crítica.
