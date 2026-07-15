# Spec-guia — Coerência sistêmica

**Status:** aprovada · **Data:** 2026-07-14
**Função deste documento:** fixar propósito, invariantes e critérios para as
specs executáveis de redução de complexidade. Esta spec não autoriza alterações
de comportamento por si só.

---

## 1. Contexto

O Radar de Editais nasceu como laboratório de capacitação em RAG e agentes de
escrita. A partir desse núcleo, tornou-se um produto com três capacidades de
negócio igualmente relevantes:

1. **Mapeamento:** compreender oportunidades, programas, investidores, ICTs e
   agências do ecossistema brasileiro de inovação;
2. **Match/Radar:** cruzar o contexto de uma empresa com oportunidades e atores,
   explicando afinidade, elegibilidade e evidências; e
3. **Escrita:** transformar uma oportunidade escolhida em proposta ou pitch com
   RAG, agentes especializados e decisão humana.

O sistema continua tendo propósito duplo:

- **produto:** ser utilizável por empresas/startups no ciclo mapear → avaliar
  fit → construir proposta;
- **laboratório e portfólio:** permitir estudar, comparar e demonstrar
  arquiteturas multiagênticas, retrieval, avaliação e operação de sistemas de
  IA aplicados a um domínio real.

A complexidade funcional necessária a esses objetivos não é um defeito. O
problema a tratar é a complexidade que impede uma pessoa de compreender o
produto ou um técnico de identificar o caminho autoritativo do sistema.

## 2. Resultado pretendido

Preservar a profundidade do laboratório e todas as capacidades válidas do
produto, enquanto:

- o usuário de produto encontra poucas portas de entrada e entende a próxima
  ação sem conhecer a arquitetura;
- o usuário técnico consegue rastrear cada superfície até dados, runtime,
  configuração e avaliação correspondentes;
- cada responsabilidade possui um caminho canônico claramente documentado; e
- história, experimentos e capacidades opcionais continuam acessíveis sem
  competir com o estado atual.

Princípio central:

> **Simplificar a compreensão e a exposição, não empobrecer a capacidade.**

## 3. Perfis atendidos

### 3.1 Usuário de produto

Empresa, startup ou profissional de inovação que quer:

- compreender o ecossistema e descobrir caminhos possíveis;
- encontrar oportunidades compatíveis com seu contexto;
- avaliar evidências, elegibilidade, prazo e requisitos; e
- construir uma proposta ou pitch.

Esse usuário não precisa entender gold SQL, RAG híbrido, LangGraph, tiers de
LLM, workers ou evals para completar sua jornada.

### 3.2 Usuário técnico

Mantenedor, pesquisador, recrutador ou avaliador do portfólio que quer:

- compreender as decisões arquiteturais;
- executar o sistema e reproduzir avaliações;
- observar como agentes, retrieval, dados e gates humanos se combinam; e
- distinguir runtime atual, capacidade experimental e registro histórico.

### 3.3 Operador

Papel privilegiado exercido pelo mantenedor: revisa Descobertas, promove
evidências, acompanha pipeline e opera deploy. É uma superfície operacional,
não uma quarta proposta de valor para o usuário final.

## 4. Modelo do sistema

```text
Fontes + Descoberta com gate humano
  → conhecimento confiável e pesquisável
      ├─ Mapeamento: compreender o ecossistema
      ├─ Match/Radar: avaliar aderência ao contexto da empresa
      └─ Escrita: construir proposta/pitch sobre a oportunidade escolhida

Laboratório técnico
  → instrumenta e avalia dados, retrieval e agentes nas três capacidades
```

### 4.1 Capacidades de negócio

| Capacidade | Pergunta do usuário | Superfícies atuais | Resultado |
|---|---|---|---|
| Mapeamento | “O que existe e como este ecossistema se organiza?” | Explorar `/`, Ecossistema `/oportunidades`, fichas | entendimento de oportunidades e atores |
| Match/Radar | “O que faz sentido para esta empresa e por quê?” | `/radar`, cards e perfil | conjunto priorizado com evidência e elegibilidade |
| Escrita | “Como transformar esta escolha em uma proposta defensável?” | Projetos `/projects`, `/workspace/{sessionId}`, library e checklist | rascunho iterável, fundamentado e revisável |

### 4.2 Capacidades transversais

- ingestão multi-fonte, Descoberta e promoção;
- catálogo gold relacional e documentos canônicos;
- retrieval, embeddings, RAG e rerank;
- runtime agêntico, tools, memória e human-in-the-loop;
- perfil e biblioteca por workspace;
- avaliação, observabilidade e operação multi-tenant.

Essas capacidades não precisam ser expostas como jornadas independentes ao
usuário de produto.

## 5. Tipos de complexidade

### 5.1 Complexidade essencial — preservar

Decorre do problema real: fontes heterogêneas, documentos longos, incerteza de
elegibilidade, matching semântico, autoria assistida, segurança multi-tenant e
decisão humana sobre resultados de IA.

### 5.2 Complexidade de exposição — organizar

O sistema pode ser profundo sem mostrar todos os conceitos, controles e estados
ao mesmo tempo. A técnica preferencial é **progressive disclosure**: primeiro a
decisão necessária; depois evidências, detalhes e ferramentas avançadas.

### 5.3 Complexidade acidental — reduzir

- mais de uma representação autoritativa para o mesmo dado;
- caminhos de runtime substituídos ainda apresentados como atuais;
- documentação histórica competindo com documentação operacional;
- configurações combinatórias sem perfil suportado;
- harnesses paralelos sem decisão ou golden;
- scaffolds cujo estado e gatilho não estão explícitos; e
- nomenclatura técnica vazando para a jornada do produto sem necessidade.

## 6. Invariantes

Qualquer spec filha deve preservar, salvo decisão explícita e independente:

1. as três capacidades de negócio e o valor laboratorial do sistema;
2. gold SQL como fonte operacional de catálogo e match;
3. RAG sobre documentos canônicos de fontes nativas ou evidência promovida;
4. Descoberta como staging com gate humano antes de publicação;
5. afinidade não é probabilidade de aprovação;
6. elegibilidade desconhecida nunca elimina uma empresa;
7. “AI drafts, humans decide” em perfil, promoção, match e escrita;
8. isolamento por workspace e contratos de segurança existentes;
9. experimentos não alteram o runtime produtivo sem spec e gate de avaliação; e
10. capacidades opcionais falham isoladamente, sem quebrar o núcleo.

## 7. Eixos executáveis

Esta spec orienta cinco eixos. Uma spec filha só deve ser criada quando o eixo
estiver pronto para execução; não é necessário detalhar todos antecipadamente.

| Ordem | Eixo | Resultado esperado | Risco predominante |
|---:|---|---|---|
| 1 | Autoridade documental | estado atual localizável sem reconstruir a história | baixo |
| 2 | Modelo mental e superfície do usuário | três jornadas claras com progressive disclosure | médio, UX |
| 3 | Avaliação e operação | experimentos reproduzíveis e gates inequívocos | médio, integração |
| 4 | Convergência de runtime e dados | um caminho canônico por responsabilidade | médio/alto, regressão |
| 5 | Capacidades dormentes | estado, gatilho e custo explícitos para cada scaffold | variável |

Impacto e reversibilidade ordenam entregas dentro de um eixo; não são fronteiras
de documentação.

## 8. Contrato das specs filhas

Cada spec executável deve declarar:

- problema comprovado e evidência;
- perfil afetado: produto, técnico ou operador;
- capacidade preservada;
- comportamento atual e comportamento pretendido;
- fora de escopo;
- contratos e invariantes afetados;
- impacto: baixo, médio ou alto;
- reversibilidade e estratégia de migração;
- validação proporcional, incluindo eval quando tocar IA; e
- critérios objetivos de conclusão e remoção de legado.

Specs devem ser delimitadas por uma responsabilidade coesa, não por diretório
ou pela conveniência de agrupar muitas mudanças num único PR.

## 9. Autoridade documental pretendida

| Documento | Autoridade |
|---|---|
| `README.md` | proposta do sistema, arquitetura resumida e início rápido |
| `AGENTS.md` | execução, validação e cuidados operacionais para agentes/mantenedores |
| `docs/architecture.md` | runtime e fluxos atuais |
| `WIKI.md` + `wikis/` | domínio, vocabulários e regras lidas pelo código |
| `docs/specs/` | contratos ativos ou ainda vigentes |
| `docs/historical/` | decisões e implementações substituídas, sem autoridade atual |

Um fato atual deve ter uma fonte autoritativa e as demais páginas devem apontar
para ela, não reescrevê-lo em versões concorrentes.

## 10. Critérios globais de sucesso

O trabalho de coerência estará concluído quando:

1. as três capacidades de negócio puderem ser explicadas sem vocabulário de
   implementação e tenham próxima ação explícita;
2. superfícies administrativas não sejam confundidas com proposta de valor do
   produto;
3. um usuário técnico consiga ir de uma superfície ao runtime e ao gate de
   avaliação autoritativos sem encontrar caminhos concorrentes;
4. toda spec em `docs/specs/` tenha status e autoridade claros;
5. representações ou flags legadas só permaneçam com consumidor e razão
   documentados;
6. experimentos relevantes terminem em suíte, decisão registrada ou histórico;
7. capacidades dormentes tenham gate explícito e não pareçam parcialmente
   ativas; e
8. nenhuma simplificação remova capacidade válida apenas para reduzir contagem
   de arquivos, módulos, dependências ou conceitos.

## 11. Fora de escopo desta spec-guia

- redesenhar navegação ou telas;
- alterar ranking, elegibilidade, RAG ou comportamento de agentes;
- remover `kg_store`, flags, tabelas, scaffolds ou documentação;
- criar novas funcionalidades;
- escolher providers/modelos;
- executar migrações de dados; e
- definir antecipadamente a implementação completa dos cinco eixos.

## 12. Specs filhas

| Eixo | Spec | Estado |
|---|---|---|
| Autoridade documental | [`document-authority.md`](document-authority.md) | vigente |
| Modelo mental e superfície do usuário | [`user-mental-model.md`](user-mental-model.md) | vigente |
| Avaliação e operação | [`evaluation-operations.md`](evaluation-operations.md) | aprovada |
| Convergência de runtime e dados | [`data-plane-convergence.md`](data-plane-convergence.md) | vigente |

Os demais eixos só recebem uma spec quando o eixo anterior tiver sido executado
e reconciliado, salvo bloqueio comprovado ou independência explícita.
