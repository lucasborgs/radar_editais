# Investigação — Neo4j LLM Graph Builder

**Data:** 2026-08-08
**Escopo:** avaliar se o Neo4j LLM Graph Builder pode criar automaticamente o
KG do Radar, sua licença e sua integração com a
[spec de evolução do ecossistema](../specs/knowledge-ecosystem-evolution.md).

## Conclusão executiva — perspectiva do produto

Sob a perspectiva do produto, o Graph Builder pode melhorar significativamente
o Radar se a co-localização de documentos, chunks, embeddings e entidades no
Neo4j produzir melhores respostas, explicações e caminhos de execução com
menor complexidade operacional. A separação atual entre KG e RAG é uma hipótese
arquitetural, não um requisito de produto.

O ponto decisivo não é preservar ou rejeitar chunks no grafo. É medir se uma
arquitetura GraphRAG unificada melhora as verticais principais:

- Explorer: descoberta de entidades, vizinhança e caminhos relevantes;
- Writing: recuperação de evidências, grounding e citações;
- Match: recall semântico sem degradar elegibilidade e precisão;
- Discovery: velocidade para transformar fontes novas em conhecimento útil.

O Graph Builder é um candidato forte para acelerar essa hipótese, mas ainda não
é, sozinho, o sistema completo de governança e qualidade necessário para o
produto.

Ele automatiza a extração de entidades e relações de documentos não
estruturados, mas não elimina a necessidade de uma ontologia para um KG de
produção. A própria documentação recomenda configurar schema de nós e relações
para obter maior qualidade. Sem schema, o resultado é uma extração genérica,
não o modelo institucional, temporal e normativo exigido pelo Radar.

## 1. Cria KG sem ontologia ou schema?

**Parcialmente.** O aplicativo permite construir um grafo sem schema explícito,
usando transformadores baseados em LLM. Também permite:

- escolher um schema predefinido;
- fornecer um schema próprio;
- carregar o schema existente do banco;
- fornecer uma descrição textual ou ontologia para o LLM sugerir um schema.

Contudo, a documentação oficial afirma que a extração tem maior qualidade
quando os tipos de nós e relações são configurados. Portanto, o modo sem schema
é adequado para exploração, não para garantir a ontologia canônica do Radar.

O fato de o Graph Builder armazenar `Document`, `Chunk`, embeddings, entidades
e relações no Neo4j não deve ser classificado como incompatibilidade por si só.
Pode ser uma vantagem de produto: o sistema consegue atravessar diretamente
entidade → relação → documento → chunk e combinar contexto estrutural e
semântico em uma mesma recuperação. A desvantagem precisa ser medida em custo,
latência, atualização, escalabilidade e qualidade, não presumida.

## 2. É open source?

**Sim, o repositório do aplicativo é publicado sob Apache-2.0.** O repositório
oficial também documenta execução local com Docker Compose.

Isso não significa que todos os modelos, serviços externos, dados processados
ou a oferta hospedada tenham as mesmas condições. O custo e a licença dos LLMs,
embeddings, Neo4j utilizado e fontes continuam sendo decisões separadas.

## 3. Pode ser integrado ao Radar?

**Sim.** Há três níveis possíveis, que devem ser comparados por experimento:

### A. Spike de extração

Executar o Graph Builder sobre um corpus pequeno do Radar e comparar:

- precisão de entidades;
- precisão de relações;
- aderência à ontologia;
- custo e latência;
- cobertura de evidências;
- estabilidade entre reprocessamentos.

### B. GraphRAG unificado

Adotar o padrão do Builder como arquitetura de serving, mantendo no Neo4j:

- documentos e chunks;
- embeddings;
- entidades e relações;
- ligações entre entidades e evidências textuais.

Essa opção pode tornar o Explorer mais natural e simplificar consultas híbridas
do Writing e do Match. Também pode reduzir divergência entre índice vetorial e
grafo. Deve ser comparada com a alternativa de vector store separado usando
queries reais do Radar.

### C. Produtor de propostas

Usar o transformador do projeto para gerar `DraftNodes` e `DraftRelations`.
Essas propostas passariam pelo fluxo próprio do Radar:

```text
documento canônico
→ Graph Builder/transformer
→ DraftNodes/DraftRelations
→ validação e revisão humana
→ publicação no Neo4j canônico
```

Nesse modelo, o Graph Builder não publica diretamente uma verdade ativa.

### D. Fork ou reutilização parcial

O código Python/FastAPI e as integrações LangChain podem servir como referência
ou ser incorporados seletivamente. Ainda seria necessário adaptar o fluxo para
idempotência, versões, proveniência por afirmação, estados `draft/reviewed/active`,
supersessão, regras rígidas e critérios específicos do Radar.

## O que ele não substitui

O Graph Builder não substitui, sem adaptação significativa:

- adapters determinísticos por fonte;
- classificação entre baixo e alto risco;
- revisão humana de regras jurídicas e critérios complexos;
- separação entre instituição, programa e oportunidade;
- árvore de elegibilidade `AND`/`OR`/`NOT`;
- temporalidade e supersessão de fatos;
- distinção entre credenciamento, competência e histórico;
- manifests, releases, retries e reprocessamento do plano de dados;
- contratos próprios de `DocumentoFonte`, `Evidencia`, `DraftNodes` e
  `DraftRelations`.

## Avaliação orientada ao produto

O spike deve comparar pelo menos duas arquiteturas sobre o mesmo corpus e as
mesmas perguntas:

1. Neo4j com entidades/relações e chunks/embeddings integrados;
2. Neo4j separado de um vector store de chunks/embeddings.

Medir:

- precisão e recall de entidades, relações e caminhos;
- recall de evidências e qualidade das citações na escrita;
- precisão, recall e falsos positivos/negativos no matching;
- qualidade do Explorer em perguntas de entidade e de caminho;
- latência p50/p95, custo de ingestão e custo de consulta;
- tempo para atualizar, corrigir e reprocessar uma fonte;
- consistência entre grafo, texto e embeddings;
- esforço operacional, backup e recuperação;
- taxa de fatos ou relações incorretos gerados pelo LLM.

O resultado pode justificar a adoção de chunks e embeddings dentro do Neo4j,
fora dele ou em uma arquitetura híbrida. A decisão deve entrar na spec como
resultado do benchmark, não como premissa anterior ao benchmark.

O Graph Builder deve ser avaliado como possível acelerador do produto e como
possível base de uma arquitetura GraphRAG unificada. Mesmo no melhor cenário,
ontologia, governança, revisão de alto risco, temporalidade e qualidade das
regras continuam sendo responsabilidades do Radar.

## Fontes primárias

- [Documentação oficial do LLM Knowledge Graph Builder](https://neo4j.com/labs/genai-ecosystem/llm-graph-builder/)
- [Documentação oficial de features](https://neo4j.com/labs/genai-ecosystem/llm-graph-builder-features/)
- [Repositório oficial](https://github.com/neo4j-labs/llm-graph-builder)
- [Licença Apache-2.0 no repositório](https://github.com/neo4j-labs/llm-graph-builder/blob/main/LICENSE)
- [Código do transformador LLM no repositório](https://github.com/neo4j-labs/llm-graph-builder/blob/main/backend/src/llm.py)
