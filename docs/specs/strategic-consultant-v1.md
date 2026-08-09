# Spec mandatória — Consultor estratégico v1

**Status:** aprovada e mandatória · **Data:** 2026-08-08
**Documento de evolução:**
[`knowledge-ecosystem-evolution.md`](knowledge-ecosystem-evolution.md)
**Documentos de produto relacionados:**
[`product-strategy-ecosystem-pathways.md`](product-strategy-ecosystem-pathways.md)
e [`product-pathways-domain-matching.md`](product-pathways-domain-matching.md)

## 1. Autoridade

Esta é a especificação mandatória da próxima evolução do Radar. Ela define a
experiência de produto, o vocabulário mínimo, os contratos entre capacidades e
as invariantes que a implementação deve respeitar.

Em caso de conflito com specs anteriores sobre orquestração, caminhos, escopo do
catálogo ou papel do Knowledge Graph, este documento prevalece. A spec de
evolução registra possibilidades futuras e não cria requisitos para concluir
esta versão.

## 2. Objetivo de produto

O Radar deve atuar como um consultor estratégico de inovação. A experiência não
termina em uma lista de editais: ela transforma uma intenção ainda incompleta em
um projeto compreendido, apresenta caminhos viáveis e acompanha a escolha até um
próximo passo ou artefato executável.

```text
conversa
→ brief revisável
→ projeto de inovação
→ caminhos de inovação
→ escolha do usuário
→ aprofundamento e decisão
→ próximo passo ou escrita fundamentada
```

O sistema deve conectar as capacidades inteligentes que já possui em uma
jornada contínua. A prioridade é entregar inteligência útil com uma arquitetura
simples, compreensível e adequada a um produto pré-beta mantido por um
desenvolvedor solo.

## 3. Escopo ativo

Esta versão contempla:

- oportunidades de financiamento público não reembolsável e outros apoios
  públicos à inovação;
- desafios corporativos e inovação aberta;
- aceleradoras e incubadoras;
- ICTs, laboratórios e capacidades tecnológicas;
- formação e evolução de projetos de inovação;
- recomendações de caminhos, lacunas, parceiros e próximos passos;
- escrita de propostas e outros artefatos apoiada por RAG.

Estão fora do escopo ativo:

- linhas de crédito e instrumentos reembolsáveis;
- investidores privados, venture capital, investidores-anjo e fundraising;
- bolsas puramente acadêmicas sem participação empresarial;
- submissão automática de propostas;
- aconselhamento jurídico autônomo.

Esses itens não devem aparecer como recomendações nas superfícies ativas.

## 4. Princípios mandatórios

1. **Jornada antes da infraestrutura:** decisões técnicas são julgadas pela
   qualidade da consultoria entregue.
2. **LangGraph no coração da experiência:** o workflow agentivo conduz a
   conversa, preserva o contexto, escolhe capacidades e acompanha decisões.
3. **Capacidades profundas, interfaces pequenas:** KG, Match, extração e RAG
   executam trabalhos especializados por contratos explícitos.
4. **Um contexto compartilhado:** Explore, avaliação de caminhos e Writing não
   podem reconstruir versões incompatíveis da empresa ou do projeto.
5. **Conhecimento não é recomendação:** fatos, inferências e orientações são
   representados e comunicados separadamente.
6. **Desconhecido não é inelegível:** ausência de informação produz lacuna e
   próximo passo, nunca eliminação silenciosa.
7. **Recall e precisão dependem da decisão:** exploração privilegia não perder
   caminhos potenciais; elegibilidade e escrita exigem evidência mais forte.
8. **RAG e KG têm papéis complementares:** o KG organiza o que se sabe; o RAG
   recupera o conteúdo necessário para fundamentar interpretação e escrita.
9. **Migração substitui, não acumula:** quando uma fatia nova atende a jornada,
   o caminho legado equivalente deve ser retirado.
10. **Pré-beta proporcional:** testes representativos e smokes da jornada são
    suficientes; robustez corporativa não é condição de entrega.

## 5. Linguagem do domínio

### `EmpresaCliente`

Organização atendida pelo Radar. Contém sua identidade estável, sem confundir a
empresa com declarações temporais sobre seu perfil.

### `PerfilEmpresa`

Retrato factual atual da empresa: localização, porte, estágio, setores,
tecnologias, capacidades e restrições conhecidas. Pode conter desconhecidos e
deve indicar origem e atualização quando isso afetar uma decisão.

### `BriefProjeto`

Hipótese conversacional, revisável e ainda incompleta sobre a intenção do
usuário. Organiza problema, solução, objetivo, contexto e lacunas antes que o
usuário confirme a criação de um projeto.

### `ProjetoInovacao`

Objeto de trabalho estável, confirmado pelo usuário. Reúne o problema, a
solução, tecnologias, maturidade, impacto esperado, necessidades, restrições e
decisões relevantes. Pode evoluir sem perder sua identidade.

### `CaminhoInovacao`

Objeto central da experiência. É uma hipótese estratégica que conecta uma
empresa e um projeto a uma oportunidade, instituição, capacidade ou combinação
de atores, junto de:

- fatos que sustentam o caminho;
- inferências e grau de confiança;
- requisitos e lacunas;
- riscos e perguntas pendentes;
- próximos passos;
- possíveis artefatos de execução.

Um caminho não é um resultado efêmero de busca. Ele pode ser proposto,
comparado, selecionado, aprofundado, reavaliado ou descartado.

### `Oportunidade`

Possibilidade concreta e temporal de apoio, participação ou colaboração. Deve
ser distinguida de programas permanentes, instituições mantenedoras e canais de
inovação.

### `AtorEcossistema`

Instituição que participa de um caminho, como agência, ICT, laboratório,
aceleradora, incubadora ou organização promotora. O papel exercido no caminho
deve ser explícito.

### `Afirmação`

Algo declarado por uma fonte sobre um sujeito. É usada quando o conteúdo é
governado, temporal, contraditório ou importante para uma decisão.

### `Evidência`

Trecho e localização de fonte que sustentam uma afirmação. Um link genérico não
é evidência suficiente para uma regra ou condição crítica.

### `FatoCanônico`

Afirmação aceita como visão operacional vigente. É uma projeção atual e pode ser
substituída quando surgir informação mais recente ou confiável.

### `Inferência`

Conclusão derivada de fatos ou relações. Deve explicitar sua base e nunca ser
apresentada como declaração da fonte.

### `Recomendação`

Orientação estratégica produzida pelo consultor. Combina fatos, inferências,
preferências e risco, mas não entra no KG como fato.

## 6. Jornada mandatória

### 6.1 Compreender

O consultor recebe uma intenção, pergunta ou projeto existente. Ele usa o perfil
disponível, identifica o que já sabe e pergunta apenas o que pode mudar
materialmente os caminhos sugeridos.

### 6.2 Formar o brief

O sistema transforma a conversa em um `BriefProjeto` visível e revisável. O
usuário pode corrigir premissas antes da materialização do projeto.

### 6.3 Confirmar o projeto

O `BriefProjeto` só se torna `ProjetoInovacao` após confirmação explícita. A
conversa pode continuar sem essa confirmação, mas não deve criar silenciosamente
um projeto definitivo.

### 6.4 Propor caminhos

O consultor combina perfil, projeto e conhecimento do ecossistema para propor
`CaminhoInovacao`. Cada proposta deve explicar por que existe, o que é fato, o
que foi inferido e o que ainda precisa ser descoberto.

### 6.5 Aprofundar e escolher

O usuário pode comparar, investigar ou selecionar um caminho. A seleção é uma
decisão persistente e passa a orientar novas pesquisas, avaliação de
elegibilidade e escrita.

### 6.6 Executar

O caminho escolhido conduz a um próximo passo concreto: validar uma regra,
buscar uma parceria, organizar documentação, preparar uma abordagem ou abrir um
artefato de escrita fundamentado.

## 7. `ConsultantGraph`

O `ConsultantGraph` é o orquestrador central da experiência. Ele não substitui
os módulos especializados nem transforma todas as operações em agentes.

Suas responsabilidades são:

- interpretar a intenção em contexto;
- manter continuidade entre turnos;
- formar e revisar o brief;
- solicitar confirmação humana nos pontos de decisão;
- materializar e atualizar o projeto;
- escolher quais capacidades consultar;
- propor, comparar e aprofundar caminhos;
- manter a escolha do usuário;
- encaminhar o caminho para execução ou escrita.

Estado conceitual mínimo:

```text
workspace
empresa/perfil atual
conversa e intenção corrente
brief em formação ou revisão
projeto confirmado
caminhos propostos
caminho selecionado
lacunas e decisões pendentes
próximo passo ou artefato em andamento
```

O estado guarda objetos e referências essenciais. Documentos, chunks, grafo,
artefatos completos e logs operacionais permanecem nos stores responsáveis.

O LLM decide como conduzir uma conversa ambígua e quais ferramentas utilizar. As
ferramentas aplicam contratos, filtros temporais, regras duras e persistência de
forma determinística quando a natureza da operação exigir.

## 8. Módulos e interfaces

### 8.1 `DocumentIntelligence`

Transforma uma fonte em um pacote de conhecimento evidenciado.

```text
ingest(documento) → documento canônico + conteúdo estruturado + evidências
```

Na versão mandatória, pode reutilizar a ingestão atual. A extração adaptativa é
uma evolução prioritária, mas não bloqueia a jornada inicial.

### 8.2 `Knowledge`

Oferece conhecimento consultável sem expor o mecanismo de armazenamento.

```text
search(intenção, filtros) → entidades e sinais
get(entidade) → fatos, relações e evidências
paths(âncoras, objetivos) → conexões e explicações estruturais
```

O consumidor não deve depender de SQL, Cypher, tabelas gold ou detalhes de
índice. O backend pode evoluir sem alterar o contrato de produto.

### 8.3 `Pathways`

Transforma conhecimento e contexto em hipóteses estratégicas persistentes.

```text
propose(empresa, projeto) → caminhos
select(caminho) → caminho selecionado
reassess(caminho, novo_contexto) → caminho atualizado
```

O módulo pode usar recuperação semântica, regras, travessia de grafo e avaliação
por LLM. Não existe obrigação de preservar as fases atuais do Match.

### 8.4 `ConsultantGraph`

Consome as três interfaces anteriores, administra a conversa e mantém a jornada
coerente. É a única autoridade para a progressão conversacional entre brief,
projeto, caminhos e execução.

### 8.5 `GroundedWriting`

Abre a escrita a partir do contexto escolhido, não apenas de um `edital_id`.

```text
open(caminho, tipo_artefato) → sessão
turn(sessão, instrução) → revisão do artefato
review(sessão) → crítica e lacunas
```

O planejamento usa o projeto, o caminho, requisitos e fatos do KG. A redação usa
RAG sobre documentos da oportunidade, evidências, materiais autorizados da
empresa e contexto do projeto.

## 9. Contratos centrais

### 9.1 `BriefProjeto`

Deve conter, quando conhecidos:

- intenção original;
- problema e usuários afetados;
- hipótese de solução;
- tecnologias e capacidades;
- objetivo de inovação;
- estágio e maturidade;
- localização e restrições;
- dúvidas que podem mudar os caminhos;
- estado de revisão.

### 9.2 `ProjetoInovacao`

Acrescenta ao brief confirmado:

- identidade persistente;
- versão e histórico de decisões relevantes;
- vínculo com empresa e perfil utilizado;
- necessidades tecnológicas e de parceria;
- impacto, orçamento e cronograma quando disponíveis;
- caminhos associados.

### 9.3 `CaminhoInovacao`

Deve conter:

- identidade e estado;
- empresa, projeto e contexto usados;
- tipo de caminho;
- oportunidade e atores envolvidos;
- fatos confirmados e respectivas evidências;
- inferências e bases utilizadas;
- requisitos, lacunas, riscos e confiança;
- situação temporal;
- próximo passo recomendado;
- artefatos possíveis;
- decisão do usuário e data da última avaliação.

Os contratos devem ser compartilhados entre API, orquestrador, avaliação de
caminhos e escrita. Nenhum consumidor mantém uma versão privada incompatível.

## 10. Conhecimento, RAG e memória

### 10.1 Knowledge Graph

O KG organiza entidades, relações e fatos usados para navegar o ecossistema. A
versão mandatória não exige a troca imediata do armazenamento atual. O contrato
`Knowledge` é a fronteira que permite melhorar ou substituir essa implementação.

### 10.2 RAG

O RAG recupera texto necessário para comprovar, interpretar e escrever. Ele não
é a autoridade sobre a identidade das entidades nem transforma texto recuperado
automaticamente em fato canônico.

### 10.3 Memória

A memória preserva conversa, decisões, preferências e continuidade do trabalho.
Ela não substitui o perfil, o projeto, o caminho, o KG ou as evidências. Uma
lembrança não vira fato do ecossistema sem o fluxo correspondente.

## 11. Confiança, evidência e temporalidade

O produto utiliza quatro níveis de conhecimento:

1. **oficial confirmado:** sustentado por fonte oficial suficiente;
2. **corroborado:** sustentado por múltiplas fontes consistentes;
3. **inferido como sinal:** útil para exploração, mas não confirmado;
4. **draft aguardando revisão:** extraído ou proposto, ainda não publicável como
   verdade operacional.

Exploração pode utilizar todos os níveis com linguagem proporcional. Gates de
elegibilidade e escrita factual só podem tratar uma regra como confirmada quando
sua evidência for adequada.

Validade temporal deve ser explícita. Ausência de prazo não significa fluxo
contínuo; conflito ou insuficiência gera `needs_review`; oportunidade encerrada
não pode ser apresentada como ativa.

Toda resposta estratégica deve separar:

- fatos confirmados;
- relações ou conclusões derivadas;
- lacunas e incertezas;
- recomendação do consultor.

## 12. Verticais representativas

A implementação deve provar a jornada em duas verticais, sem amarrá-la a uma
fonte específica.

### 12.1 Caminho normativo

Financiamento público não reembolsável com documento formal, regras, prazo,
retificações, elegibilidade, possível ICT/laboratório e escrita fundamentada.

### 12.2 Caminho aberto

Desafio corporativo ou inovação aberta descoberto em páginas web, possivelmente
sem edital formal, com múltiplas fontes e próximo passo de mercado.

As duas verticais devem percorrer conversa, brief, projeto, caminhos, escolha e
execução. Elas existem para verificar generalidade, não para criar duas
arquiteturas.

## 13. Migração e simplificação

A migração ocorre por fatias completas da jornada:

1. introduzir o contrato novo atrás de uma fronteira clara;
2. conectar uma vertical ponta a ponta;
3. observar a experiência com casos representativos;
4. promover o caminho novo;
5. retirar o fluxo legado equivalente.

Não é obrigatório manter formatos, fases do Match, consultas do Explore ou
entradas da Writing atuais. A fundação útil pode ser reaproveitada, mas nenhuma
decisão anterior é preservada por inércia.

É proibido manter indefinidamente dois fluxos de produto concorrentes. A
coexistência só é aceitável durante uma substituição curta e reversível.

## 14. Critérios de aceite

Esta versão estará cumprida quando:

1. uma conversa formar um brief revisável;
2. o usuário puder confirmar um projeto persistente;
3. o sistema propuser caminhos rastreáveis usando o mesmo projeto e perfil;
4. cada caminho separar fatos, inferências, lacunas e recomendação;
5. o usuário puder selecionar um caminho e retomar essa decisão depois;
6. o caminho escolhido gerar um próximo passo concreto;
7. a escrita puder começar a partir do caminho e usar RAG fundamentado;
8. as duas verticais representativas funcionarem ponta a ponta;
9. estados desconhecidos e conflitos forem comunicados sem falsa certeza;
10. linhas de crédito, investidores e bolsas acadêmicas fora do escopo não
    aparecerem nas superfícies ativas;
11. o fluxo legado substituído por cada fatia for removido;
12. a solução permanecer operável e compreensível por um desenvolvedor solo.

## 15. Não objetivos desta versão

- migrar obrigatoriamente o KG para Neo4j;
- colocar documentos, chunks e embeddings no mesmo banco;
- adotar o Neo4j LLM Graph Builder em produção;
- modelar toda regra ou afirmação possível antes da jornada;
- construir uma plataforma genérica de data releases;
- implementar object storage, dead-letter e backfill como pré-requisitos
  universais;
- criar um estado universal para todos os workflows;
- transformar Discovery e avaliação de elegibilidade em agentes;
- reescrever simultaneamente Explore, Match, Writing e toda a ingestão;
- alcançar cobertura nacional completa antes de aprender com o produto.
