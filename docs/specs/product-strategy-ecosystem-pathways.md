# Spec — Estratégia de produto e caminhos do ecossistema

**Status:** aprovada · **Data:** 2026-08-04  
**Documento-pai:** [`system-coherence.md`](system-coherence.md)  
**Substitui:** posicionamentos anteriores que tratavam financiamento público,
investimento privado, desafios, aceleradoras, incubadoras e ICTs como um único
catálogo de oportunidades  
**Perfis afetados:** usuário de produto, operador e usuário técnico  
**Impacto:** estratégico; evolução incremental dos fluxos existentes de
descoberta, match e escrita

## 1. Decisão

O Radar é uma plataforma de inteligência e execução para empresas brasileiras
de base tecnológica encontrarem, avaliarem e executarem caminhos de inovação no
ecossistema brasileiro.

Seu núcleo é o acesso a financiamento público, apoio à inovação e capacidade
tecnológica. O produto também contempla desafios corporativos, aceleradoras,
incubadoras e parcerias com ICTs, mas cada categoria conserva sua própria lógica
de acesso, elegibilidade, relacionamento e resultado.

Investidores privados e funcionalidades de fundraising com investidores estão
fora do produto ativo. Dados, fontes, entidades, prompts, matching, telas e
fluxos exclusivamente relacionados a investidores devem ser desativados ou
retirados das superfícies ativas, conforme os planos de implementação desta
spec.

## 2. Problema que a decisão resolve

O modelo anterior tratava mecanismos heterogêneos como se fossem instâncias do
mesmo objeto `oportunidade`. Isso produzia recomendações pouco confiáveis:

- um edital público possui critérios documentais e, frequentemente, processo
  explícito de submissão;
- uma linha de crédito depende também de maturidade financeira, receita,
  garantias e capacidade de pagamento;
- um desafio corporativo é uma porta de inovação aberta, contratação ou
  validação de mercado, não necessariamente financiamento;
- uma aceleradora ou incubadora oferece desenvolvimento, rede e suporte, não
  necessariamente capital; e
- uma ICT ou um laboratório oferece capacidade de execução ou parceria, não é
  uma chamada à qual a empresa simplesmente se candidata.

Misturar esses objetos em um score único faz o sistema parecer mais abrangente,
mas reduz sua confiabilidade e obscurece o próximo passo real da empresa.

## 3. Usuário e problema-alvo

O usuário primário é uma empresa brasileira de base tecnológica — startup, PME
ou empresa inovadora — que deseja:

- financiar um projeto de inovação;
- encontrar apoio, infraestrutura ou competência tecnológica;
- descobrir oportunidades de mercado ou inovação aberta; ou
- entender quais caminhos de desenvolvimento são plausíveis para seu estágio.

O sistema não pressupõe que a empresa já tenha um projeto completamente
definido. Ela pode entrar com uma intenção, capacidade, desafio ou necessidade
vaga e usar o sistema para transformar isso em um projeto ou caminho de ação.

Essa mudança é uma evolução do produto existente, não uma reconstrução do
runtime. Descoberta, match e escrita continuam sendo as três capacidades
principais e preservam seus pipelines, Knowledge Graph, RAG, agentes,
avaliações e contratos sempre que eles continuarem adequados ao novo escopo.

## 4. Proposta de valor

> Ajudar uma empresa a transformar uma necessidade de inovação em um caminho
> viável de financiamento, parceria, desenvolvimento ou acesso ao mercado,
> com evidências, requisitos, incertezas e próximos passos explícitos.

O produto deve explicar não apenas que algo parece compatível, mas também:

- qual é a natureza do caminho;
- por que ele é relevante;
- quais critérios foram confirmados;
- quais informações continuam desconhecidas;
- que lacunas impedem ou dificultam o avanço;
- quais atores ou capacidades são necessários; e
- qual ação a empresa deve tomar em seguida.

Afinidade não é probabilidade de aprovação, investimento ou contratação.

## 5. Domínios ativos

### 5.1 Financiamento e apoio público

Fontes e instrumentos prioritários:

- FINEP;
- BNDES, nas linhas relacionadas à inovação;
- todas as FAPs brasileiras;
- EMBRAPII;
- bolsas e programas correlatos de apoio à pesquisa, desenvolvimento e
  inovação;
- subvenção econômica; e
- crédito para inovação.

O catálogo de FAPs não deve ser limitado às fontes já maduras. A cobertura de
cada FAP deve ser declarada, observada e graduada por qualidade e atualidade,
sem apresentar ausência de dados como ausência de oportunidades.

Crédito permanece no escopo, mas nunca deve ser recomendado somente por
compatibilidade temática. O sistema deve considerar, quando houver evidência,
receita, estágio financeiro, garantias, contrapartida, capacidade de pagamento,
destinação do recurso e requisitos do agente financeiro.

### 5.2 Desafios corporativos e inovação aberta

Desafios corporativos representam caminhos de validação, parceria, contratação
ou acesso a mercado. O sistema deve informar, quando possível:

- problema ou tema proposto;
- empresa promotora;
- perfil de solução buscada;
- estágio ou formato de participação;
- benefício ou resultado esperado;
- forma de inscrição ou contato; e
- se existe premiação, contrato, piloto, investimento ou apenas conexão.

Não se deve inferir que um desafio oferece financiamento ou que a participação
gera investimento.

### 5.3 Aceleradoras e incubadoras

Aceleradoras e incubadoras são caminhos de desenvolvimento empresarial. O
sistema deve distinguir, quando possível:

- estágio aceito;
- duração e formato do programa;
- apoio oferecido;
- existência de investimento ou bolsa;
- participação societária ou outras contrapartidas;
- localização e modalidade;
- processo de entrada; e
- adequação ao momento da empresa.

Investimento eventualmente associado ao programa não transforma a entidade em
um objeto de fundraising para o produto.

### 5.4 ICTs, laboratórios e infraestrutura tecnológica

ICTs, laboratórios, pesquisadores e equipamentos são capacidades de execução e
parceria. Eles devem ser conectados a projetos, problemas tecnológicos,
competências e instrumentos de apoio.

O PNIPE (`https://pnipe.mcti.gov.br/search?term=&type=LAB`) é uma fonte
prioritária para infraestrutura laboratorial brasileira. Sua integração deve
preservar a proveniência da instituição, laboratório, equipamento, competência,
localização e condições de acesso, quando disponíveis.

O sistema não precisa esperar um catálogo nacional completo para oferecer valor.
Deve priorizar a descoberta de ICTs e laboratórios relevantes para um projeto,
uma oportunidade ou uma competência solicitada.

## 6. Investidores: decisão de desativação

Ficam fora do escopo ativo:

- fundos de venture capital, investidores-anjo e family offices como destinos
  recomendados;
- matching empresa–investidor;
- tese, estágio, cheque ou pipeline de fundos;
- preparação de fundraising e introduções para investidores;
- fontes e entidades cuja única função no produto era representar investidores;
  e
- linguagem de “captação”, “rodada” ou “investimento” quando usada para
  significar fundraising privado.

Termos como “investimento” podem permanecer quando fizerem parte da descrição
oficial de um programa, aceleradora ou instrumento público, mas devem ser
classificados segundo a natureza real do caminho e não como venture capital.

A desativação deve ser reversível no armazenamento quando isso for útil para
histórico e migração, mas os dados não podem alimentar catálogo, matching,
busca, ranking, prompts ou interfaces ativas.

## 7. Modelo mental do produto

O objeto central da experiência é um **caminho de inovação**, que pode envolver
um ou mais objetos de domínio:

```text
intenção ou projeto
  → caminho de inovação
      ├─ financiamento ou apoio público
      ├─ desafio ou acesso ao mercado
      ├─ incubação ou aceleração
      ├─ ICT, laboratório ou parceiro tecnológico
      └─ plano de ação e candidatura/contato
```

Um caminho não precisa ser uma sequência automática nem uma promessa de
resultado. É uma hipótese explicada, baseada em evidências, que pode exigir
decisão e contato humano.

## 8. Jornadas de entrada

### 8.1 Projeto definido

```text
perfil da empresa
  → projeto de inovação
  → caminhos compatíveis
  → explicação da compatibilidade
  → lacunas de elegibilidade e prontidão
  → ICTs/parceiros necessários
  → plano de candidatura, inscrição ou contato
```

### 8.2 Exploração de possibilidades

```text
perfil, capacidades e intenções
  → possibilidades relevantes do ecossistema
  → problemas, capacidades e oportunidades relacionadas
  → hipóteses de projeto ou caminho
  → opções de financiamento, parceria, mercado ou desenvolvimento
  → brief de projeto e próximo passo
```

Na jornada exploratória, o sistema deve declarar que está apresentando
possibilidades e hipóteses, não recomendações de elegibilidade já confirmada.
Seu trabalho é ajudar a empresa a formular um projeto que ainda não estava
claro.

Exemplos de entradas válidas:

- “Quero reduzir o consumo energético da minha operação, mas não sei que
  projeto desenvolver.”
- “Tenho uma tecnologia de sensores e quero encontrar aplicações e parceiros.”
- “Preciso validar um material em laboratório.”
- “Quero entender quais programas poderiam apoiar minha próxima etapa.”

## 8.3 Artefatos de candidatura e RAG de escrita

RAG continua sendo parte do produto, mas entra principalmente depois que um
caminho foi escolhido ou estruturado. O sistema deve tratar “proposta” como uma
família de artefatos, e não como um formato universal:

| Caminho | Artefato provável |
|---|---|
| subvenção FINEP/FAP | proposta técnica e plano de trabalho |
| crédito para inovação | projeto financiável, orçamento e justificativa |
| desafio corporativo | resposta ao desafio, solução ou pitch |
| aceleradora | formulário, deck ou carta de intenção |
| incubadora | plano de desenvolvimento ou candidatura |
| ICT | conceito de projeto, escopo técnico e plano de parceria |

O corpus do RAG de escrita deve combinar documentos da oportunidade, contexto do
caminho e conhecimento da empresa. Para documentos de oportunidade, deve
recuperar regulamentos, manuais, critérios, formulários, anexos e FAQs; para a
empresa, deve recuperar perfil, projetos, capacidades, documentos e biblioteca
de conteúdos.

RAG não substitui o KG na descoberta ou seleção do caminho. O KG responde fatos
e relações estruturadas; o RAG recupera evidências e contexto documental; o
assistente produz um artefato revisável e fundamentado. Uma solicitação de
crédito que não exigir proposta não deve forçar a abertura de uma sessão de
escrita completa.

## 9. Contrato de matching e explicação

Não existe um ranking universal entre todos os domínios. Cada caminho deve ser
avaliado com critérios próprios:

| Domínio | Critérios centrais | Resultado esperado |
|---|---|---|
| financiamento público | elegibilidade, instrumento, projeto, prazo, contrapartida | candidatura viável ou lacunas claras |
| crédito | projeto, maturidade financeira, destinação, garantias e pagamento | adequação financeira, não só temática |
| desafio corporativo | problema, solução, estágio, formato e benefício | inscrição, contato ou descarte explicado |
| aceleradora/incubadora | estágio, setor, programa, contrapartida e suporte | programa adequado ou incompatibilidades |
| ICT/laboratório | competência, equipamento, localização, acesso e projeto | parceiro/capacidade plausível |

O resultado deve ser explicável e incluir evidências. Dados desconhecidos não
devem ser tratados como negativos sem justificativa; devem aparecer como
incertezas ou perguntas pendentes.

## 10. Descoberta e Deep Research

Deep Research entra como um novo canal de descoberta e investigação de fontes,
sem substituir integralmente os scrapers determinísticos. Scrapers continuam
adequados para portais conhecidos e estáveis, enquanto Deep Research é útil
para linhas de crédito, páginas desestruturadas, desafios corporativos,
aceleradoras, incubadoras, ICTs e descoberta de novas fontes.

Ele não é a fonte canônica do catálogo. O fluxo normativo permanece:

```text
pesquisa e descoberta
  → staging com evidências
  → revisão humana
  → documentos canônicos e normalização
  → gold/Knowledge Graph
  → catálogo, Explorar e Radar
```

O piloto deve comparar Deep Research com os coletores atuais em cobertura,
precisão, duplicação, atualidade, custo, citações e capacidade de extração
estruturada. A adoção deve ser incremental e não pode remover gates humanos ou
proveniência.

## 11. Papel do Knowledge Graph

O Knowledge Graph é a memória estruturada e evolutiva do ecossistema. Seu valor
não está em reunir o maior número de entidades, mas em preservar relações
explicáveis entre:

- empresa e projeto;
- projeto e necessidade tecnológica;
- necessidade e competência;
- competência e ICT/laboratório;
- projeto e instrumento de apoio;
- empresa e requisitos de prontidão;
- desafio e problema de mercado; e
- programa e caminho de candidatura, inscrição ou contato.

O grafo deve suportar arranjos compostos sem transformá-los em certezas. Toda
relação relevante precisa indicar origem, atualidade e grau de interpretação.

## 12. Fora de escopo

- fundraising privado e relacionamento com investidores;
- promessa de aprovação, contratação, aceleração ou parceria;
- score único para todos os tipos de caminho;
- completude imediata do mapeamento de ICTs e laboratórios;
- catálogo nacional perfeito antes de validar uma jornada vertical;
- automação de submissão, contato ou negociação sem decisão humana;
- transformar toda intenção vaga em projeto automaticamente; e
- remover o pipeline de proveniência e revisão em favor de uma resposta única
  gerada por LLM.

## 13. Critérios de sucesso

O novo modelo estará representado no produto quando:

1. o usuário conseguir iniciar com um projeto definido ou com uma intenção
   exploratória;
2. o sistema distinguir financiamento, crédito, desafio, aceleradora,
   incubadora e capacidade de ICT/laboratório;
3. investidores não aparecerem em busca, matching, catálogo ou interface ativa;
4. FINEP, BNDES, todas as FAPs, EMBRAPII e programas correlatos puderem ser
   classificados por instrumento e cobertura declarada;
5. uma recomendação explicar evidências, desconhecidos, lacunas e próximo passo;
6. ICTs e laboratórios puderem ser encontrados por competência relacionada ao
   projeto, incluindo dados provenientes do PNIPE quando disponíveis;
7. uma exploração sem projeto puder resultar em um brief de projeto ou em um
   mapa de possibilidades sem falsa precisão; e
8. a qualidade for medida por utilidade e confiabilidade, não apenas pelo
   número de entidades ou oportunidades coletadas.

## 14. Métricas orientadoras

- percentual de registros ativos com fonte oficial e data de verificação;
- taxa de falso positivo por tipo de caminho;
- percentual de recomendações com próximo passo acionável;
- tempo até a empresa formular um brief de projeto;
- oportunidades descartadas por incompatibilidade explicada;
- ICTs/laboratórios identificados e efetivamente considerados pela empresa; e
- candidaturas, inscrições, contatos ou parcerias iniciados — sem atribuir ao
  Radar resultados que não possam ser comprovados.

## 15. Evolução incremental

Esta spec define o contrato de produto. A implementação deve preservar os três
fluxos existentes e pode evoluir suas frentes quase independentemente:

### Descoberta

- desativar investidores e fontes exclusivamente relacionadas a eles;
- adicionar Deep Research como canal complementar;
- manter scrapers, staging, revisão humana, ETL e proveniência;
- aceitar caminhos além de editais sem exigir a reconstrução do pipeline.

### Match

- manter o funil e os componentes atuais quando forem adequados;
- impedir investidores de aparecerem em resultados ativos;
- adicionar o tipo de caminho e explicações específicas por domínio;
- não criar um score universal nem reescrever o ranking sem necessidade
  comprovada.

### Escrita

- manter WritingSession, RAG, chunks, biblioteca e agentes;
- adicionar tipo de artefato ou caminho de forma aditiva;
- preservar o fluxo de propostas;
- especializar respostas a desafios, candidaturas e projetos para ICT somente
  quando houver necessidade concreta.

As três frentes compartilham apenas um contrato mínimo de caminho:

```text
caminho = tipo + entidade + objetivo + requisitos + canal de acesso
          + evidências + status
```

Não é necessário redesenhar todo o Knowledge Graph, fazer uma auditoria geral
ou reconstruir o sistema antes de implementar essa evolução. O produto está em
pré-beta; decisões podem ser validadas por fatias funcionais e uso observado.

Nenhuma etapa deve reativar investidores ou criar um score universal sem uma
nova decisão de produto registrada em spec.
