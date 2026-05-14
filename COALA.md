# CoALA — Cognitive Architectures for Language Agents

> Sumers, T. R., Yao, S., Narasimhan, K., & Griffiths, T. L. (2024).  
> *Cognitive Architectures for Language Agents.* arXiv:2309.02427 (v3, março 2024).

---

## 1. O que é CoALA e para que serve

CoALA é um **framework conceitual** que organiza e sistematiza o projeto de *language agents* — sistemas de IA que combinam LLMs com memória externa, fluxos de controle internos e interação com ambientes. O objetivo central é oferecer uma linguagem comum para:

- **Comparar** agentes existentes de forma rigorosa (ReAct, Voyager, Generative Agents, etc.)
- **Identificar lacunas** no espaço de design ainda inexploradas
- **Guiar o projeto** de novos agentes de forma modular e sistemática


**Definição de language agent (do paper):**  
> Sistemas de IA que usam LLMs para interagir com o mundo, conectando-os a memória interna e a ambientes externos, ancorando-os a conhecimento existente ou observações externas.


---

## 2. Arquitetura do CoALA — visão geral

O framework organiza qualquer language agent em **três dimensões ortogonais**: Memória, Ações e Tomada de Decisão.

```
┌─────────────────────────────────────────────────────────────┐
│                     LANGUAGE AGENT                          │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                  MEMÓRIA DE TRABALHO                  │  │
│  │  (variáveis ativas: percepções, metas, conhecimento  │  │
│  │   recuperado, histórico recente de ações)             │  │
│  └────────────┬─────────────────────────┬───────────────┘  │
│               │ ler/escrever            │ ler/escrever      │
│  ┌────────────▼──────────────────────────────────────────┐  │
│  │              MEMÓRIA DE LONGO PRAZO                   │  │
│  │  ┌─────────────┐ ┌──────────────┐ ┌───────────────┐  │  │
│  │  │  Episódica  │ │  Semântica   │ │  Procedural   │  │  │
│  │  │ (trajetórias│ │(fatos, mundo │ │(LLM weights + │  │  │
│  │  │  histórico) │ │  e self)     │ │ código fonte) │  │  │
│  │  └─────────────┘ └──────────────┘ └───────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              ESPAÇO DE AÇÕES                          │  │
│  │                                                       │  │
│  │  Internas:                   Externas:                │  │
│  │  ┌──────────┐ ┌──────────┐   ┌──────────────────┐   │  │
│  │  │Raciocínio│ │Recuperação│  │   Grounding       │   │  │
│  │  │(→ memória│ │(memória  │   │ (físico, diálogo, │   │  │
│  │  │de trab.) │ │LP→ trab.)│   │  digital)         │   │  │
│  │  └──────────┘ └──────────┘   └──────────────────┘   │  │
│  │  ┌──────────────────────┐                            │  │
│  │  │ Aprendizado          │                            │  │
│  │  │ (→ memória LP)       │                            │  │
│  │  └──────────────────────┘                            │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              TOMADA DE DECISÃO                        │  │
│  │                                                       │  │
│  │   Ciclo: PROPOSTA → AVALIAÇÃO → SELEÇÃO → EXECUÇÃO   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Módulos de Memória — detalhamento

Os quatro módulos de memória têm fundamento direto na psicologia cognitiva:

| Módulo | Referência cognitiva | Conteúdo | Operações | Risco de modificação |
|---|---|---|---|---|
| **Memória de Trabalho** | Baddeley & Hitch (1974) | Variáveis ativas da iteração corrente: percepções, metas, planos, recuperações | Leitura/escrita contínuas pelo LLM | Baixo (volátil por design) |
| **Memória Episódica** | Nuxoll & Laird (2007) | Trajetórias passadas, histórico de interações, pares de treino | Escrita via aprendizado; leitura via recuperação | Médio |
| **Memória Semântica** | Lindes & Laird (2016) | Fatos sobre o mundo, conhecimento de domínio, inferências destiladas | Inicializada de BDs externas; escrita por aprendizado LLM | Médio |
| **Memória Procedural** | Psicologia cognitiva padrão | **Implícita**: pesos do LLM; **Explícita**: código-fonte do agente (raciocínio, recuperação, grounding, decisão) | Fine-tuning (implícita); modificação de código (explícita) | **Alto** — bugs podem ser introduzidos |

### Memória de Trabalho

É o **hub central** do agente. A cada ciclo de decisão, contém:
- Entradas perceptuais do ambiente (convertidas em texto)
- Conhecimento recuperado das memórias de longo prazo
- Metas ativas e histórico recente
- Raciocínio intermediário gerado pelo LLM

Toda a comunicação entre módulos passa pela memória de trabalho. O contexto do LLM (janela de contexto) é sua implementação mais direta.

### Memória Episódica

Armazena **sequências de experiências** — o que aconteceu, quando e com qual resultado. Permite raciocínio baseado em casos: o agente recupera episódios similares ao problema atual e usa-os como guia. Implementações típicas: vetores de embeddings de trajetórias, listas de eventos com timestamps.

### Memória Semântica

Armazena **fatos descontextualizados** — conhecimento que se generaliza além de episódios específicos. Pode ser inicializada de fontes externas (Wikipedia, manuais técnicos) e atualizada com inferências do LLM. Exemplo: mapas semânticos gerados por modelos visão-linguagem.

### Memória Procedural

A mais crítica. Divide-se em:
- **Implícita**: os pesos do LLM — todo o conhecimento "cristalizado" no treinamento. Modificada por fine-tuning.
- **Explícita**: o código-fonte do agente — implementações de recuperação, raciocínio, grounding e lógica de decisão. Modificada via geração de código pelo próprio agente.

> Modificar memória procedural é o ato de maior risco em um agente: código gerado incorretamente pode quebrar o sistema inteiro.

---

## 4. Espaço de Ações — taxonomia completa

As ações se dividem em **internas** (operam sobre memória) e **externas** (interagem com o ambiente).

```
AÇÕES
├── INTERNAS
│   ├── Raciocínio
│   │   └── Atualiza memória de trabalho via processamento do LLM
│   │       (gera insights, planos, reflexões intermediárias)
│   ├── Recuperação
│   │   └── Lê memória de longo prazo → memória de trabalho
│   │       Implementações: rule-based, sparse (BM25), dense (embeddings)
│   └── Aprendizado (escrita em memória de longo prazo)
│       ├── Episódico: armazena trajetórias, histórico, pares de treino
│       ├── Semântico: destila experiências em fatos; armazena inferências LLM
│       └── Procedural
│           ├── Via atualização de parâmetros: fine-tuning supervisionado,
│           │   RLHF, feedback ambiental (mais caro computacionalmente)
│           └── Via modificação de código: atualiza templates de prompt,
│               habilidades em código, procedimentos de recuperação,
│               lógica de decisão (maior risco)
└── EXTERNAS (Grounding)
    ├── Ambiente Físico
    │   └── Comandos de manipulação robótica via linguagem;
    │       percepção via modelos visão-linguagem
    ├── Diálogo
    │   └── Comunicação com humanos ou outros agentes;
    │       pedidos de ajuda ou esclarecimento
    └── Ambiente Digital
        └── APIs, motores de jogo, websites, execução de código
```

### Ações de Recuperação — estratégias

| Estratégia | Quando usar | Exemplo |
|---|---|---|
| Rule-based | Estrutura conhecida, recuperação determinística | Busca por data/ID em BD episódica |
| Sparse (BM25, TF-IDF) | Texto, correspondência léxica importa | Busca em documentos técnicos |
| Dense (embeddings) | Semântica importa mais que léxico exato | Voyager: recupera habilidades similares |
| Combinada (recência + importância + relevância) | Memória episódica rica | Generative Agents |

### Ações de Aprendizado — trade-offs

| Tipo | Custo | Risco | Persistência | Generalização |
|---|---|---|---|---|
| Episódico | Baixo | Baixo | Alta | Baixa (específico ao episódio) |
| Semântico | Médio | Baixo | Alta | Média |
| Procedural (código) | Médio | **Alto** | Alta | Alta |
| Procedural (fine-tuning) | **Alto** | Médio | Alta | **Alta** |

---

## 5. Tomada de Decisão — o ciclo completo

O processo de decisão é estruturado como um **loop iterativo** com quatro estágios:

```
┌─────────────────────────────────────────────────────┐
│                 CICLO DE DECISÃO                    │
│                                                     │
│   ┌──────────┐     ┌──────────┐     ┌──────────┐  │
│   │ PROPOSTA │────►│AVALIAÇÃO │────►│ SELEÇÃO  │  │
│   └──────────┘     └──────────┘     └────┬─────┘  │
│        ▲                                  │         │
│        │         rejeição                 │ aprovação
│        └──────────────────────────────────┘         │
│                                           │         │
│                                   ┌───────▼──────┐  │
│                                   │  EXECUÇÃO    │  │
│                                   │(ação interna │  │
│                                   │ ou externa)  │  │
│                                   └───────┬──────┘  │
│                                           │         │
│                             observação do ambiente  │
│                                           │         │
│                              ┌────────────▼──────┐  │
│                              │ ATUALIZA MEMÓRIA  │  │
│                              │    DE TRABALHO    │  │
│                              └────────────┬──────┘  │
│                                           │         │
│                            próximo ciclo ▼         │
└─────────────────────────────────────────────────────┘
```

### Estágio de Proposta

Gera candidatos de ação via raciocínio LLM (e opcionalmente recuperação):
- **Domínios simples**: enumerar todas as ações possíveis
- **Domínios complexos**: sampling LLM, rollouts de simulador, busca em árvore

### Estágio de Avaliação

Atribui valor a cada candidato:
- Heurísticas hard-coded
- Scores do LLM (auto-avaliação)
- Valores aprendidos (value functions)
- Simulação interna (world models)

### Estágio de Seleção

Escolhe a ação a executar:
- `argmax` — greedy
- `softmax` — exploração estocástica
- Votação por maioria — múltiplos candidatos
- Rejeição — retorna à proposta se nenhum candidato é adequado

### Níveis de sofisticação de decisão

| Nível | Descrição | Exemplos |
|---|---|---|
| **Direto** | LLM gera uma ação diretamente | Chatbots simples |
| **Encadeado** | Sequência pré-definida de chamadas LLM | ReAct, Chain-of-Thought |
| **Interativo** | Loop com feedback do ambiente, planejamento adaptativo | Voyager, Generative Agents |
| **Busca em árvore** | Proposta-avaliação iterativa com BFS/DFS/MCTS | Tree of Thoughts, RAP |

---

## 6. Estudos de caso — agentes mapeados pelo CoALA

| Agente | Memória LP | Grounding externo | Ações internas | Tomada de decisão |
|---|---|---|---|---|
| **SayCan** | — (só procedural implícita) | Físico (robótica) | — | Avaliação única (LLM + value function) |
| **ReAct** | — | Digital (Wikipedia, APIs) | Raciocínio | Proposta (ciclo fixo: raciocinar → agir) |
| **Voyager** | Procedural (biblioteca de skills) | Digital (Minecraft) | Raciocínio, Recuperação, Aprendizado | Proposta + Execução + Avaliação de conclusão |
| **Generative Agents** | Episódica + Semântica | Digital + Diálogo (agentes) | Raciocínio, Recuperação, Aprendizado | Proposta + plano diário + ajuste contínuo |
| **Tree of Thoughts** | — (só memória de trabalho) | Digital | Raciocínio exclusivamente | Proposta + Avaliação + Seleção (BFS/DFS) |

### SayCan — Robótica de cozinha

- Usa 551 skills de grounding pré-definidas (sem aprendizado online)
- Decisão combina score de utilidade do LLM + score de groundability (value function aprendida por RL)
- Limitação CoALA: zero memória de longo prazo; zero ações internas → incapaz de aprender com erros

### ReAct — Pergunta-Resposta iterativo

- Ciclo fixo: raciocinar sobre situação → formular plano → gerar ação → observar resultado
- Sem avaliação explícita de candidatos: primeiro candidato é sempre executado
- Limitação: nenhum mecanismo de aprendizado; sem memória episódica/semântica

### Voyager — Exploração em Minecraft

- Inovação central: **aprendizado procedural** — o agente escreve código de novas habilidades e as armazena em biblioteca persistente
- Recuperação densa (embeddings) carrega habilidades relevantes para novas tarefas
- Permite **generalização zero-shot** via composição hierárquica de habilidades
- Primeiro agente a fechar o loop proposta → código → execução → feedback → avaliação → nova skill

### Generative Agents — Simulação social

- Inovação central: **aprendizado episódico → semântico** — o agente raciocina sobre experiências passadas e destila regras comportamentais (reflexões)
- Recuperação multi-critério: recência × importância × relevância para selecionar memórias episódicas
- Mantém plano diário em memória de trabalho e o ajusta com novas observações

### Tree of Thoughts — Resolução de problemas

- Opera sem nenhuma memória de longo prazo — demonstra que busca estruturada em memória de trabalho já supera CoT simples
- Avalia candidatos via LLM (votação ou score direto) antes de selecionar próximo passo
- Implementa BFS e DFS sobre o espaço de raciocínio

---

## 7. Lacunas identificadas no estado da arte

| Área | Lacuna | Impacto potencial |
|---|---|---|
| Recuperação adaptativa | Agentes não aprendem *quando* e *como* recuperar | Recuperação contextualmente sensível reduziria ruído no contexto |
| Aprendizado de procedimentos de recuperação | Nenhuma implementação existe | Agentes auto-otimizantes de memória |
| Atualização do processo de decisão via aprendizado | Teoricamente possível, alto risco, não implementado | Meta-aprendizado de estratégias de planejamento |
| Deleção de memória (*unlearning*) | Praticamente inexplorado | Privacidade, correção de erros factuais |
| Integração principiada recuperação + decisão | Ausente — tratados como módulos independentes | Planejamento fundamentado e verificável |
| Interação entre formas de aprendizado | Não estudada sistematicamente | Prevenção de interferência entre tipos de memória |
| Padronização de benchmarks | Inexistente para agentes (análogo ao OpenAI Gym para RL) | Comparações reproduzíveis entre agentes |

---

## 8. Insights acionáveis para quem constrói agentes

### 8.1 Escolha de memória

**Decida quais módulos seu domínio requer antes de escrever código:**

```
Domínio precisa de contexto histórico longo?     → Memória episódica
Domínio tem fatos estáveis reutilizáveis?        → Memória semântica
Domínio tem habilidades composíveis reutilizáveis? → Memória procedural explícita
Tudo se encaixa na janela de contexto?           → Só memória de trabalho
```

**Implementação prática:**
- Memória episódica → banco vetorial (ChromaDB, Pinecone) com embeddings de trajetórias
- Memória semântica → knowledge graph ou banco de fatos estruturado
- Memória procedural explícita → biblioteca de funções/prompts versionada com metadados de uso

### 8.2 Design do espaço de ações

**Separar explicitamente ações internas de externas reduz bugs:**
- Ações internas (reasoning, retrieval) nunca devem ter efeitos colaterais externos
- Ações de aprendizado precisam de validação antes de escrever em memória LP
- Grounding deve converter toda observação multimodal em texto antes de entrar na memória de trabalho

**Regra prática:** use LLM para raciocínio zero-shot flexível; use código explícito para algoritmos que LLMs executam mal (busca em árvore, verificação de constraints, ordenação determinística).

### 8.3 Tomada de decisão

**Não implemente busca complexa antes de validar o básico:**

```
1. Comece com proposta direta (LLM → ação)
2. Adicione ciclo de avaliação quando o agente errar sistematicamente
3. Adicione busca em árvore apenas quando avaliação single-step for insuficiente
4. Adicione aprendizado procedural apenas quando o domínio for suficientemente estável
```

**Avaliação:** auto-avaliação do LLM é frágil — use value functions aprendidas (como SayCan) ou verificadores simbólicos quando possível.

### 8.4 Aprendizado — ordem de menor para maior risco

```
Menor risco  ──────────────────────────────────►  Maior risco
[Episódico]  [Semântico]  [Proc. fine-tuning]  [Proc. código]
```

- **Episódico**: adicione primeiro — custo baixo, reversível
- **Semântico**: adicione quando fatos se repetem entre episódios
- **Fine-tuning**: apenas quando o comportamento precisa mudar de forma permanente e você tem dados suficientes
- **Geração de código**: implemente validação estrita antes de executar código gerado

### 8.5 Recuperação

**Implemente recuperação multi-critério para memória episódica:**
```python
score(memória) = α * recência + β * importância + γ * relevância_semântica
```
Ajuste α, β, γ por domínio. Generative Agents usa pesos iguais como ponto de partida.

**Para memória procedural (skills):** recuperação densa por embeddings é superior à busca léxica — habilidades com nomes diferentes podem ser semanticamente equivalentes.

### 8.6 Métricas de monitoramento por módulo

| Módulo | Métricas a monitorar |
|---|---|
| Memória de trabalho | Taxa de overflow de contexto; informação descartada prematuramente |
| Memória episódica | Precisão de recuperação @K; staleness de trajetórias antigas |
| Memória semântica | Taxa de conflito (fatos contraditórios); cobertura do domínio |
| Memória procedural | Taxa de falha de skills; frequência de uso por skill |
| Decisão | Taxa de rejeição de candidatos; distribuição de profundidade de busca |

---

## 9. Relação com sistemas RAG e prompt engineering

CoALA fornece o contexto mais amplo no qual RAG e prompt engineering se encaixam:

| Técnica | Componente CoALA | Limitação que CoALA endereça |
|---|---|---|
| RAG (Retrieval-Augmented Generation) | Ação de recuperação + memória semântica/episódica | RAG puro não aprende — não escreve de volta em memória |
| Prompt chaining | Sequência de ações de raciocínio | Sem avaliação de candidatos → sem correção adaptativa |
| Few-shot prompting | Recuperação episódica hard-coded | Exemplos estáticos; agente não aprende novos exemplos |
| Fine-tuning | Aprendizado procedural implícito | Custo alto; sem granularidade de quais comportamentos mudar |
| Tool use / function calling | Ações externas de grounding | Sem memória das experiências com tools anteriores |

---

## 10. Contribuições conceituais centrais

1. **LLM como sistema de produção probabilístico** — a analogia com Post e Markov não é metafórica: é formal. Compreender isso permite importar décadas de pesquisa em controle de fluxo de produções.

2. **Distinção memória de trabalho vs. longo prazo** — a janela de contexto do LLM é uma *implementação* da memória de trabalho, não toda a memória do agente.

3. **Aprendizado como escrita em memória** — toda forma de aprendizado em agents pode ser reduzida a: escrever em qual módulo de memória? Com qual custo/risco?

4. **Tomada de decisão como ciclo proposta-avaliação-seleção** — explicita que agentes "simples" (ReAct) pulam avaliação e seleção, o que limita sua robustez.

5. **Modularidade como princípio de design** — separar memória, ação e decisão em módulos distintos permite substituição, teste e evolução independentes de cada componente.

---

## 11. Conclusão

CoALA contextualiza language agents modernos dentro da história da IA e da ciência cognitiva, oferecendo um vocabulário preciso para projeto e comparação. O framework sugere que **inteligência geral baseada em linguagem** pode emergir do design cuidadoso de arquiteturas modulares inspiradas em décadas de trabalho em sistemas simbólicos — não apenas de escalar LLMs maiores.

Para quem constrói agentes: o valor imediato de CoALA é a **checklist de design** que ele implica:

```
□ Quais módulos de memória meu domínio precisa?
□ Quais ações são internas vs. externas?
□ Meu agente precisa de avaliação de candidatos ou proposta direta é suficiente?
□ Que forma de aprendizado (se alguma) é necessária e seu custo/risco é justificado?
□ Como meu agente vai recuperar informação de memória de longo prazo?
□ Tenho métricas para cada módulo separadamente?
```

---

*Paper: https://arxiv.org/abs/2309.02427*  
*GitHub (coleção de language agents organizada via CoALA): referenciado no paper*
