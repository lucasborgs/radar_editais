# Spec — Modelo mental e superfície do usuário

**Status:** vigente · **Data:** 2026-07-14
**Documento-pai:** [`system-coherence.md`](system-coherence.md)
**Perfis afetados:** usuário de produto; secundariamente operador
**Impacto:** médio; navegação, nomenclatura e composição de superfícies, sem
alteração dos contratos de domínio ou dos pipelines de IA

## 1. Problema comprovado

O produto possui três capacidades de negócio igualmente relevantes, mas a
superfície atual não as apresenta como um modelo coerente:

- a entrada `/` funciona como exploração conversacional e construção de perfil,
  mas não recebe um nome estável na navegação;
- `/oportunidades` se chama **Oportunidades**, embora catalogue também programas,
  investidores e ICTs;
- **Radar** aparece no header da entrada, mas não na navegação lateral que reúne
  as demais áreas;
- a entrada pode renderizar uma lista de matches, enquanto `/radar` também se
  apresenta como superfície completa de resultados;
- sessões de escrita aparecem no histórico sob o verbo **Escrever**, enquanto a
  capacidade produz tanto propostas quanto pitches e não possui um destino
  primário próprio na navegação;
- o workspace usa **Explorer** para navegação/estrutura interna, ao mesmo tempo
  que a jornada de mapeamento também é descrita como Explorer; e
- o retorno do workspace é rotulado `Radar`, mas aponta para `/`, a superfície de
  exploração.

Essas constatações vêm das rotas e componentes atuais, especialmente
`frontend/src/app/page.tsx`, `frontend/src/app/radar/page.tsx`,
`frontend/src/app/oportunidades/page.tsx`,
`frontend/src/components/layout/ConversationSidebar.tsx`,
`frontend/src/components/frontdoor/FrontDoorHeader.tsx` e
`frontend/src/components/workspace/`. A inspeção da aplicação publicada em
2026-07-14 confirmou que as divergências estão expostas ao usuário, não apenas
presentes em código dormente.

O problema não é a existência de várias capacidades ou superfícies. É a falta de
uma hierarquia que explique onde a pessoa está, qual resultado encontrará ali e
qual é a próxima ação possível.

## 2. Resultado pretendido

O usuário deve reconhecer três destinos primários, em ordem compatível com a
jornada, sem que a ordem imponha um fluxo obrigatório:

1. **Explorar:** compreender o ecossistema e formar contexto;
2. **Radar:** avaliar o que tem aderência à empresa e por quê; e
3. **Projetos:** desenvolver propostas e pitches a partir de uma escolha.

As capacidades continuam acessíveis de forma independente quando houver
contexto suficiente. A interface deve revelar detalhes técnicos, evidências e
ferramentas avançadas apenas quando forem úteis à decisão corrente.

## 3. Decisões de vocabulário

| Conceito | Nome de produto | Uso pretendido |
|---|---|---|
| capacidade de mapeamento | **Explorar** | ação e destino primário; entrada `/` |
| catálogo amplo | **Ecossistema** | título da visão que reúne oportunidades, programas, investidores e ICTs |
| capacidade de match | **Radar** | destino primário e superfície canônica dos resultados priorizados |
| capacidade de escrita | **Projetos** | destino primário para propostas e pitches em andamento |
| ambiente interno de autoria | workspace | termo técnico interno; não é a proposta de valor na navegação |
| painel de seções do projeto | **Estrutura** | evita competir com o nome da jornada Explorar |
| descoberta de fontes | **Descobertas** | superfície operacional restrita; não é capacidade primária do produto |

Regras de linguagem:

- **Explorar** é verbo de jornada e pode nomear a entrada; **Ecossistema** é o
  substantivo que nomeia o catálogo amplo;
- **Radar** significa afinidade explicada, nunca chance de aprovação;
- **Projetos** é o contêiner persistente; **escrever**, **revisar** e **exportar**
  são ações dentro dele; e
- nomes de implementação como gold, RAG, LangGraph, workspace, pipeline de
  ingestão e Explorer interno não devem ser necessários para escolher uma das
  três jornadas.

## 4. Arquitetura de informação pretendida

### 4.1 Navegação primária

A navegação global deve apresentar, no mesmo nível:

| Destino | Rota canônica | Promessa ao usuário |
|---|---|---|
| Explorar | `/` | perguntar, compreender o ecossistema e completar contexto |
| Radar | `/radar` | ver e comparar aderências explicadas |
| Projetos | `/projects` | retomar propostas e pitches |

A implementação pode preservar aliases e rotas internas existentes. A criação
do destino de listagem de Projetos é apenas composição sobre sessões já
persistidas; não autoriza um novo fluxo de escrita nem um novo modelo de dados.
Se não houver uma listagem reutilizável segura, a primeira entrega pode usar o
agrupamento existente do histórico como destino, desde que **Projetos** continue
sendo um conceito primário e não um link quebrado.

### 4.2 Navegação secundária

Permanecem como suporte às três jornadas:

- **Perfil**;
- **Pipeline**;
- **Arquivos**; e
- **Configurações**.

Esses itens não competem visualmente com as três capacidades. **Descobertas**
permanece visível apenas para operador autorizado.

### 4.3 Shells focados

Radar e Projetos podem manter shells focados quando isso protege a atenção da
pessoa. Ainda assim, devem oferecer orientação global consistente e retornos
semanticamente corretos. Um controle rotulado **Radar** deve levar ao Radar; um
retorno para `/` deve ser rotulado **Explorar**.

## 5. Contrato de cada superfície

### 5.1 Explorar

`/` é a porta de entrada canônica. Continua permitindo conversa aberta,
exploração do domínio e formação/refino do perfil.

Pode:

- responder perguntas e apresentar entidades ou oportunidades contextuais;
- mostrar uma prévia de que há aderências disponíveis; e
- conduzir explicitamente ao Radar quando o perfil mínimo estiver pronto.

Não deve apresentar uma segunda lista completa e concorrente de resultados de
match. Cards contextuais continuam válidos, mas ordenação, filtros, comparação e
conjunto priorizado pertencem ao Radar.

### 5.2 Ecossistema

A visão atualmente em `/oportunidades` é a exploração estruturada do catálogo.
Seu título de produto passa a ser **Ecossistema** porque seu conteúdo inclui
oportunidades e atores.

Filtros por tipo preservam **Oportunidades**, **Programas**, **Investidores** e
**ICTs** como categorias de domínio. A rota pode permanecer `/oportunidades`
para evitar migração sem valor imediato; nome visível e URL não precisam mudar
juntos.

### 5.3 Radar

`/radar` é a superfície canônica de match. Concentra:

- carregamento e atualização do conjunto priorizado;
- afinidade e evidências;
- elegibilidade e incertezas;
- filtros e comparação; e
- transição para um Projeto.

Sem perfil mínimo, o Radar continua explicando o requisito e conduzindo a
**Explorar**. Afinidade permanece descrita como evidência de escopo, não promessa
de aprovação.

### 5.4 Projetos

**Projetos** reúne o trabalho persistente de proposta e pitch. O workspace
existente continua sendo o ambiente de execução de um projeto, com RAG, agentes,
estrutura, checklist e exportação preservados.

A pessoa entra em um projeto a partir de uma oportunidade escolhida ou retoma um
projeto existente. Esta spec não autoriza iniciar escrita sem oportunidade,
alterar templates, fundir modos, mudar o runtime agêntico nem criar um novo tipo
de documento.

O painel interno hoje chamado **Explorer** deve receber um nome que descreva sua
função local; **Estrutura** é o padrão desta spec. Identificadores de código podem
permanecer até que uma renomeação seja segura e útil.

## 6. Progressive disclosure

Cada superfície deve responder primeiro à decisão do seu nível:

| Nível | Decisão principal | Detalhes revelados depois |
|---|---|---|
| Explorar | o que investigar ou informar | entidades, relações e dados do perfil |
| Radar | o que merece avaliação | trechos, critérios, incertezas e comparação |
| Projetos | o que construir ou revisar | fontes, estrutura, ferramentas agênticas e checklist |

Informação técnica continua disponível para demonstração e pesquisa, mas não
deve ser pré-requisito cognitivo para avançar. Progressive disclosure não
significa esconder evidência, limitações ou incerteza da IA.

## 7. Capacidade e invariantes preservados

Esta mudança preserva:

- as três capacidades de negócio com igual importância;
- a exploração conversacional e o catálogo amplo;
- o contrato de perfil mínimo do Radar;
- ranking, elegibilidade, evidências, filtros e comparação existentes;
- os fluxos de proposta e pitch, seus agentes, RAG, memória e checklist;
- contratos de API, schemas, migrations e persistência de sessões;
- Descoberta com gate humano e superfícies operacionais; e
- o laboratório técnico como parte explícita do propósito do sistema.

Aplicam-se integralmente as invariantes da
[`spec-guia`](system-coherence.md#6-invariantes).

## 8. Fora de escopo

- redesenhar a identidade visual ou o design system;
- alterar match, elegibilidade, retrieval, prompts ou agentes;
- criar recomendação automática, novo onboarding ou novo fluxo de produto;
- migrar URLs apenas para refletir novos rótulos;
- substituir o perfil ou a biblioteca;
- remover rotas e aliases antes de provar ausência de consumidores; e
- expandir a área de Projetos além das capacidades já implementadas.

## 9. Plano de execução

### Etapa 1 — Vocabulário e navegação

1. apresentar Explorar, Radar e Projetos como destinos primários consistentes;
2. mover utilidades e operação para níveis secundário e restrito;
3. trocar o título visível do catálogo para Ecossistema; e
4. corrigir links cujo rótulo e destino expressem capacidades diferentes.

### Etapa 2 — Responsabilidade das superfícies

1. tornar o Radar o destino explícito quando o perfil estiver pronto;
2. limitar resultados na entrada a contexto ou prévia, sem duplicar a experiência
   completa de match;
3. preservar a listagem, filtros e comparação no Radar; e
4. expor retomada de propostas e pitches sob Projetos, reutilizando sessões.

### Etapa 3 — Vocabulário local de autoria

1. substituir **Explorer** por **Estrutura** na interface do projeto;
2. revisar textos visíveis que usem workspace ou outros termos de implementação;
3. manter nomes internos quando a troca não trouxer benefício ao usuário; e
4. verificar estados vazio, não autenticado, perfil incompleto e projeto ativo.

As etapas podem virar commits ou PRs separados. Nenhuma etapa depende de alterar
o runtime de IA.

## 10. Reversibilidade e migração

- rótulos e composição de navegação são reversíveis sem migração de dados;
- rotas canônicas existentes permanecem válidas;
- aliases só podem ser removidos após inventário de consumidores;
- sessões de escrita existentes devem continuar retomáveis; e
- qualquer destino novo para Projetos deve reutilizar APIs e persistência atuais
  ou ser retirado da entrega.

## 11. Validação

Para cada etapa implementada:

- `git diff --check`;
- `cd frontend && npx tsc --noEmit`;
- testes direcionados de componentes, navegação e helpers afetados;
- inspeção manual responsiva dos estados autenticado e não autenticado;
- inspeção dos estados: perfil vazio, perfil pronto, Radar sem resultados, Radar
  com resultados, projeto em andamento e histórico vazio;
- confirmação de que todos os rótulos de retorno correspondem ao destino;
- confirmação de que matches completos possuem uma única superfície canônica; e
- `git status --short` para proteger os artefatos locais de avaliação.

Como esta spec não muda modelos nem pipelines de IA, ela não exige nova suíte de
eval. Qualquer implementação que ultrapasse esse limite deve parar e ganhar
contrato e validação próprios.

## 12. Critérios de conclusão

O eixo estará concluído quando:

1. Explorar, Radar e Projetos forem reconhecíveis como os três destinos
   primários;
2. Ecossistema nomear o catálogo amplo sem apagar suas categorias;
3. `/radar` for a única superfície completa de resultados de match;
4. a entrada conduzir explicitamente ao Radar quando houver perfil suficiente;
5. propostas e pitches existentes puderem ser retomados sob Projetos;
6. nenhum rótulo de navegação apontar para uma capacidade diferente;
7. Descobertas permanecer separada e restrita à operação;
8. a pessoa conseguir atravessar Explorar → Radar → Projetos sem precisar
   compreender termos de implementação; e
9. contratos, persistência e comportamento de IA permanecerem inalterados.

## 13. Resultado da execução

Implementado em 2026-07-14:

- Explorar, Radar e Projetos passaram a formar a navegação primária;
- o catálogo amplo passou a se chamar Ecossistema, preservando a rota
  `/oportunidades` e suas categorias;
- entradas de match na conversa passaram a renderizar uma prévia com CTA, sem
  duplicar a lista completa, filtros e comparação de `/radar`;
- `/projects` passou a listar e retomar as sessões de proposta e pitch já
  persistidas por `GET /writing/sessions`;
- Radar e Projetos passaram a compartilhar um shell focado e responsivo;
- o painel visível do projeto passou de Explorer para Estrutura, e seus modos
  são apresentados como Contexto, Plano e Escrita sem alterar os slash commands;
- retornos e CTAs passaram a apontar para a capacidade expressa pelo rótulo; e
- APIs, schemas, migrations, ranking, agentes, retrieval e persistência não
  foram alterados.

Validação da execução: `git diff --check`, `npx tsc --noEmit`, `npm run lint`,
build de produção e inspeção local das rotas `/`, `/radar`, `/projects` e
`/oportunidades`, incluindo viewport de 390 px. A inspeção autenticada depende
de uma sessão real, mas a listagem reutiliza sem alteração o endpoint e o
contrato de sessões de escrita existentes.
