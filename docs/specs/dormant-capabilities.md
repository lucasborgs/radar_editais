# Spec — Ciclo de vida de capacidades dormentes

**Status:** vigente · **Data:** 2026-07-15
**Documento-pai:** [`system-coherence.md`](system-coherence.md)
**Perfis afetados:** usuário de produto, usuário técnico e operador
**Impacto:** médio; remove superfícies comprovadamente sem efeito e explicita o
estado de capacidades experimentais, sem ativá-las ou reduzir o propósito de
laboratório do sistema

## 1. Problema comprovado

O repositório contém capacidades profundas em estados diferentes, mas o estado
nem sempre é visível no ponto em que a capacidade aparece:

1. a tela de configurações oferece “contribuir com outcomes anônimos para
   melhorar matching global”, mas nenhum produtor ou matcher lê
   `contribute_to_global_weights`; o banco configurado possui zero opt-ins;
2. `workspaces.agent_explore_enabled` permanece no schema desde a migration 013,
   embora Explore use sempre o runtime agêntico e não exista consumidor da
   coluna; o banco possui zero valores `true`;
3. `AUTO_MEMORY_WRITE=0` congela corretamente a escrita automática, mas tasks,
   endpoints e comentários de fases anteriores podem fazer essa capacidade
   parecer parcialmente ativa. Há 37 `reflection_insights` históricos, dos
   quais 34 ativos, e a leitura desses insights continua sendo runtime vivo;
4. `playbook_overlays` e `meta_reflection_runs` possuem tabelas e caminho de
   leitura, mas nenhum job produtor. Ambas as tabelas estão vazias;
5. o extrator agêntico de perfil é executável sob
   `AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED=true`, porém permanece desligado por
   padrão e sua suíte é diagnóstica, sem gate aceito;
6. `CNPJ_LOOKUP_ENABLED` habilita uma tool subordinada ao extrator agêntico,
   também desligada e sem contrato operacional corrente; e
7. o backend local por `sentence_transformers` permanece como seam experimental
   após a remoção do braço Gemma, mas trocar dimensões continua exigindo migration
   e avaliação.

Ao mesmo tempo, flags como Deep Research no Explore, fontes da Descoberta e
reranking possuem produtores, consumidores e degradação explícitos. Ser
opcional ou estar desligado em uma implantação não torna uma capacidade
dormente.

## 2. Resultado pretendido

Uma pessoa técnica deve conseguir classificar qualquer capacidade como:

| Estado | Definição |
|---|---|
| `ativa` | participa do runtime padrão e possui consumidor atual |
| `opcional` | caminho executável e suportado, habilitado por configuração explícita |
| `experimental` | executável para laboratório/eval, sem contrato de produção |
| `dormente` | preservada, mas deliberadamente incapaz de produzir efeito no runtime atual |
| `obsoleta` | substituída, sem consumidor e sem estado que precise ser preservado |
| `histórica` | registro explicativo, sem código ou contrato atual |

Capacidades dormentes devem declarar responsável pela ativação, pré-condições,
custo/risco, evidência exigida e fallback. Nenhuma deve parecer disponível ao
usuário de produto antes de produzir o efeito prometido.

## 3. Classificação vigente

### 3.1 Ativas ou opcionais — preservar como estão

| Capacidade | Estado | Evidência |
|---|---|---|
| Deep Research na escrita | ativa, degradável | tool padrão do WritingAgent e staging `research_findings` |
| Deep Research no Explore | opcional | `EXPLORE_DEEP_RESEARCH_ENABLED`; mesmo produtor e fallback |
| Descoberta DOU/hub/Crawl4AI | opcional, operacional | flags no worker, staging e specs próprias |
| reranking | opcional, avaliado | `MATCH_RERANK_ENABLED`, `RERANK_BACKEND`, extra `.[rerank]` e suíte `reranker` |
| leitura de memória por workspace | ativa, degradável | `reflection_insights` + Store alimentam WritingSession; 34 insights ativos |
| leitura/auditoria de playbooks | ativa, degradável | camadas git sempre; overlays opcionais e vazios não quebram o loader |

Esses caminhos não serão removidos, ativados globalmente nem renomeados por esta
spec.

### 3.2 Experimentais — preservar sem promover

| Capacidade | Estado atual | Gate mínimo para promoção futura |
|---|---|---|
| ProfileExtractor agêntico | experimental, default off | suíte `profile_extractor` com critério aceito, orçamento/provider e revisão de privacidade do crawling |
| lookup CNPJ/BrasilAPI | experimental subordinada, default off | necessidade comprovada, contrato de indisponibilidade e medição de ganho sobre os campos do perfil |
| embeddings locais `sentence_transformers` | experimental de laboratório | dataset comparável, dimensão compatível ou migration explícita, rebuild e gate de retrieval |

Esta spec apenas torna o estado explícito em `.env.example`, `AGENTS.md` e
referências técnicas. Ativação continua exigindo uma decisão independente.

### 3.3 Dormentes — preservar congeladas

#### Escrita automática de memória por workspace

- **Estado:** `AUTO_MEMORY_WRITE=0`; produtores retornam no-op antes de LLM/DB;
  leitura de insights existentes permanece ativa.
- **Por que preservar:** há dados históricos vivos e o código implementa um
  experimento relevante de memória longitudinal do laboratório.
- **Gate:** fila de curadoria humana, TTL/decay, proveniência visível, eval de
  contaminação e isolamento por workspace.
- **Custo/risco:** chamadas LLM recorrentes, crescimento de storage e
  envenenamento silencioso do contexto de escrita.
- **Nesta spec:** não ligar a flag, não apagar insights e não alterar leitura.

#### Meta-reflexão cross-workspace e learned overlays

- **Estado:** reader e tabelas existem; não há writer/job; zero overlays e zero
  runs no banco configurado.
- **Por que preservar:** é um seam de pesquisa coerente com o propósito do
  laboratório, mas não pode aprender globalmente sem volume, consentimento e
  curadoria.
- **Gate:** spec própria com consentimento verdadeiro, anonimização verificável,
  volume mínimo de múltiplos workspaces, revisão humana de overlays, auditoria e
  eval de regressão de escrita.
- **Custo/risco:** impacto cross-tenant, perda de proveniência e propagação global
  de padrões ruins.
- **Nesta spec:** manter tabelas e leitura vazia/degradável; corrigir textos que
  afirmem que “o sistema aprende aqui” como comportamento atual.

### 3.4 Obsoletas — remover com evidência

#### Flag `agent_explore_enabled`

A coluna não possui consumidor; Explore usa sempre o agente e o banco possui
zero valores `true`. Uma migration aditiva de limpeza deve remover a coluna,
sem editar a migration histórica 013. Comentários que ainda condicionem o
runtime agêntico a flags antigas também devem ser corrigidos.

#### Consentimento `contribute_to_global_weights`

A UI, endpoint e coluna armazenam uma preferência para um processamento que não
existe. O banco possui zero opt-ins e não há consumidor fora do próprio CRUD.
Manter a superfície cria uma promessa factual falsa ao usuário.

Executado: o toggle da UI, o endpoint dedicado, os tipos associados e a coluna
foram removidos pela migration 039. Uma futura aprendizagem global deve introduzir um novo
consentimento, ligado ao processamento real e descrito no momento da coleta; não
deve herdar silenciosamente esta preferência.

## 4. Escopo de execução

1. criar uma referência curta com o inventário e as definições de estado;
2. reconciliar `README.md`, `AGENTS.md`, `.env.example`, arquitetura, memória e
   comentários de runtime;
3. remover a superfície `contribute_to_global_weights` do frontend e backend;
4. adicionar migration que remova `contribute_to_global_weights` e
   `agent_explore_enabled`, sem reescrever migrations anteriores;
5. remover comentários/tipos que ainda condicionem Writing ou Explore às flags
   aposentadas; e
6. manter defaults dormentes e cobri-los com testes direcionados.

## 5. Fora de escopo

- ativar memória automática, meta-reflexão, overlays, extrator agêntico ou CNPJ;
- apagar `reflection_insights`, tabelas de overlays ou código experimental;
- alterar Deep Research, Descoberta, reranking, embeddings de produção ou
  comportamento de agentes;
- criar fila de curadoria, TTL, anonimização ou aprendizado global;
- escolher thresholds, providers ou modelos;
- executar a migration remotamente; e
- criar roadmap ou novas funcionalidades.

## 6. Invariantes

1. leitura das 37 memórias históricas e sua projeção no Store permanecem
   funcionais;
2. `AUTO_MEMORY_WRITE=0` continua sendo o default em todos os entrypoints;
3. playbooks git continuam autoritativos e overlays vazios continuam no-op;
4. nenhuma informação de usuário passa a ser agregada entre workspaces;
5. ProfileExtractor determinístico continua sendo o caminho padrão;
6. capacidades opcionais existentes mantêm seus fallbacks; e
7. nenhuma remoção altera Radar, Explore, escrita, Descoberta ou catálogo.

## 7. Reversibilidade

- código e contratos removidos permanecem recuperáveis no histórico Git;
- a migration de drop é pequena e não transporta dados, pois ambas as colunas
  não possuem estado `true` no banco verificado;
- reintroduzir aprendizagem global exige novo contrato e nova migration, evitando
  consentimento retroativo; e
- capacidades experimentais/dormentes preservam seus módulos e dados.

## 8. Validação

- `git diff --check`;
- `ruff check .` para Python alterado;
- `cd frontend && npx tsc --noEmit`;
- testes de `/me`, auth, perfil, memória, tasks e skills/playbooks;
- busca final sem consumidores de `agent_explore_enabled` ou
  `contribute_to_global_weights` fora das migrations históricas;
- teste dos defaults `AUTO_MEMORY_WRITE=0`, profile agent off e CNPJ off;
- verificação de que o loader git-only e a leitura de insights permanecem
  inalterados; e
- confirmação de que os artefatos locais de avaliação seguem não rastreados.

## 9. Critérios de conclusão

1. nenhuma superfície de produto promete aprendizado global inexistente;
2. resíduos obsoletos comprovados saem do schema e dos contratos atuais;
3. capacidades experimentais e dormentes têm estado e gate explícitos;
4. capacidades opcionais não são confundidas com código morto;
5. memória histórica e seams de laboratório permanecem utilizáveis; e
6. a quinta spec-filha e a spec-guia podem ser reconciliadas como vigentes.

## 10. Evidência de execução

- migration 039 remove apenas `agent_explore_enabled` e
  `contribute_to_global_weights`; as migrations históricas permanecem intactas;
- a página `/settings`, seus links e o CRUD de preferência foram removidos;
- o inventário vigente está em
  [`docs/reference/capability-lifecycle.md`](../reference/capability-lifecycle.md);
- escrita automática de memória, meta-reflexão, ProfileExtractor agêntico,
  CNPJ e embeddings locais não foram ativados nem removidos; e
- os 37 insights históricos e os readers de memória/playbook foram preservados.
