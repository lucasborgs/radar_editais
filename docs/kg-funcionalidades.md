# KG: funções, tensões e rumos

> Registro da discussão sobre o papel do Grafo de Conhecimento no Radar de Editais.
> 2026-07-07

## Contexto

O hipergrado v2 (KG v2) foi desenhado como representação única do ecossistema de
inovação brasileiro, consumido por todas as camadas do sistema. Na prática, as
camadas evoluíram com exigências conflitantes, e o schema único tornou-se um
denominador comum fraco — serve todas, mas serve nenhuma bem.

## As 4 funções do KG e suas tensões

### 1. Modelo canônico do ecossistema

**O que é:** unifica em 3 tipos de nó (Oportunidade, Ator, Conceito) + 10 arestas
tudo que importa — editais, programas, investimentos, agências, ICTs,
investidores, temas, tecnologias, aplicações.

**Tensão:** o schema é permissivo demais para o match (conceitos genéricos como
"prototipagem" viram nós com o mesmo peso que "inteligência artificial") e
restritivo demais para a navegação do agente (3 tipos de nó achatam a semântica:
um programa e um edital são ambos "Oportunidade").

### 2. Substrato do match geométrico (Estágio 1)

**O que é:** os nós Conceito do KG são embedados (text-embedding-3-small) e
comparados por cosseno com os nós extraídos do perfil da empresa. O match é
puramente vetorial: `empresa_nodes × eco_nodes × cosseno ≥ 0.55 → aresta
sintética → MaxSim → ranking`.

**Tensão:** o match **ignora a topologia do grafo** — as arestas (`abrange_tema`,
`parceria_com`, `financia`) não entram no cálculo. O grafo é usado só como
contêiner de nós com embedding. Um índice plano de embedding (sem arestas, sem
tipos compostos) seria mais adequado e eficiente.

**Problema concreto identificado:** conceitos genéricos/transversais (que todo
edital tem) geram aresta sintética com o mesmo peso que conceitos específicos.
Corrigido com filtro token-level em `_is_affinity()`, mas a solução ideal seria
um peso de especificidade por conceito, não um gate binário.

### 3. Grafo de navegação para o agente LLM

**O que é:** as ferramentas do agente explorador (`get_node_neighborhood`,
`list_icts`, `explore_opportunity`) percorrem o KG via BFS, incluindo travessia
cross-source entre subgrafos (edital → catálogo ICT).

**Tensão:** o schema canônico de 3 tipos é pobre para navegação. O agente
precisaria saber:
- "Este edital exige parceria com ICT?" → aresta `exige` existe, mas o alvo é
  `ator:ict` genérico, não uma ICT nomeada
- "Quem financia?" → aresta `financia` existe
- "Qual o porte mínimo?" → é propriedade do nó (`constraints[]`), não aresta
- "Este edital pertence a que programa?" → aresta `pertence_a` existe, mas nem
  sempre populada

A informação está distribuída entre arestas, propriedades do nó e texto
residual. O agente precisa de múltiplas ferramentas para montar um quadro que
deveria vir de uma única consulta.

### 4. Catálogo curado para display

**O que é:** `hypergraph_catalog.py` expõe listas filtradas de ICTs (89
curadas), investidores e programas. A página Oportunidades e as tools do agente
usam o mesmo catálogo.

**Tensão:** o schema trata ICT, investidor, agência e corporate como `Ator`, e
programa como `Oportunidade`. Na UI o usuário vê "ICTs", "Investidores" e
"Programas" como categorias separadas — a abstração `Ator` vs `Oportunidade`
não ajuda, só confunde. O modelo canônico desconfigura o ecossistema do ponto
de vista de apresentação.

## Como a extração alimenta o problema

A extração LLM (`hyper_extractor.py`, prompt `_NODE_PROMPT`) recebe "seja
exaustivo no que importa" — sem filtro de especificidade. O mesmo prompt produz
nós para **todas as funções ao mesmo tempo**. O resultado:

- Conceitos genéricos ("prototipagem", "desenvolvimento") viram nós de
  `Conceito` com o mesmo status que conceitos específicos ("inteligência
  artificial", "diagnóstico por imagem")
- Arestas `parceria_com` apontam para `ator:ict` genérico, não para ICTs
  nomeadas (editais raramente citam parceiros específicos)
- Propriedades de negócio (prazo, valor, constraints) convivem com arestas de
  conteúdo no mesmo artefato, sem separação clara de responsabilidade

## Rumos propostos

### Diagnóstico

O hipergrado v2 tenta servir 4 mestres com um schema único, e não serve nenhum
bem. As funções precisam de artefatos diferentes:

| Função | Precisa de | Não precisa de |
|---|---|---|
| Match | Embeddings + pesos + metadados | Arestas, topologia, tipos |
| Navegação (agente) | Riqueza semântica, relações tipadas | Embeddings, scores |
| Catálogo (display) | Listas planas e confiáveis | Grafo completo |
| Modelo canônico | Integração entre fontes | Qualquer uma das acima |

### Direção

Dois artefatos a partir de uma extração LLM unificada:

```
                  ┌─── match_index.parquet ───> match (MaxSim)
                ╱
silver texto ──→┤  extração LLM
                ╲
                  └─── knowledge_graph.json ──> navegação (agente)
                                                     │
                                                     └──> catálogo (display via query)
```

**Match index:** estrutura plana, por conceito. Cada linha:
`{conceito_id, nome, descrição, dim, embedding[1536], edital_id,
peso_especificidade (0-1), domínio (agro, defesa, energia...), fonte}`. Sem
arestas, sem tipos compostos. O match vira: `empresa_nodes × match_index ×
cosseno × peso = affinity`. Rápido, ajustável, evolui independente do grafo.

**Knowledge graph:** rico em arestas, focado em navegação. O agente navega
conexões, não embeddings. Schema mais expressivo que os 3 tipos atuais
(possibilidade de tipos próprios para Programa, Investidor, ICT). Arestas mais
específicas — `parceria_com` aponta para ICTs nomeadas, não `ator:ict`
genérico. Propriedades de negócio (prazo, valor, constraints) são atributos do
nó, não nós separados.

### Próximos passos (em aberto)

- O match index como artefato separado faz sentido?
- O grafo de navegação deveria ter schema mais rico (tipos próprios) ou
  schema mais flexível (propriedades abertas)?
- A extração LLM deve continuar unificada (um prompt → nós + arestas +
  propriedades) ou bifurcar em prompts especializados (um para match, outro
  para navegação)?
- Como ponderar conceitos por especificidade sem depender de lista manual
  (IDF do corpus, embedding magnitude, LLM adjudicação)?

---

*Este documento é um registro de entendimento inicial e deve evoluir com as
decisões de design do sistema.*
