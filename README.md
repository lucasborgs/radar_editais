# Radar de Editais

**Da descoberta de uma oportunidade à primeira versão da proposta, com evidências verificáveis em cada decisão.**

O Radar ajuda startups e empresas de base tecnológica a entender o ecossistema brasileiro de fomento, encontrar editais compatíveis com seu perfil e desenvolver propostas sem esconder o texto-fonte atrás de um score de IA.

[![CI](https://github.com/lucasborgs/radar_editais/actions/workflows/ci.yml/badge.svg)](https://github.com/lucasborgs/radar_editais/actions/workflows/ci.yml)
[Arquitetura](docs/architecture.md) · [Documentação](docs/README.md) · [Preview da interface](https://radar-editais-gold.vercel.app/)

![Radar com matches e evidências para a empresa fictícia Aurora Biomateriais](docs/assets/radar-demo.jpg)

<sub>Captura local com dados inteiramente sintéticos. A preview pública atualmente demonstra o frontend; ações que dependem da API não estão disponíveis.</sub>

## O problema

Editais de fomento espalham requisitos entre anexos, páginas institucionais e vocabulários diferentes. Para uma empresa, descobrir uma chamada é apenas o começo: ainda é preciso verificar prazo e elegibilidade, relacionar o escopo à própria capacidade e transformar esse diagnóstico em uma proposta consistente.

O Radar organiza essa jornada sem tratar similaridade como promessa de aprovação. Regras eliminatórias, afinidade semântica e julgamento humano permanecem separados — e cada match mostra os trechos que o produziram.

## Três jornadas, um mesmo contexto

| Jornada | O que entrega |
| --- | --- |
| **Explorar** | Responde perguntas sobre o ecossistema e transforma a conversa, com consentimento, em um perfil estruturado da empresa. |
| **Radar** | Ordena editais, programas e investidores por compatibilidade; explicita elegibilidade, prazo e pares de evidência. |
| **Projetos** | Conduz propostas e pitches em sessões duráveis, com recuperação de fontes, crítica e checklist antes de persistir o texto. |

```mermaid
flowchart LR
    Fontes["Agências e web"] --> Catalogo["Catálogo gold<br/>Postgres + pgvector"]
    Empresa["Empresa"] --> Explorar["Explorar<br/>entender e perfilar"]
    Catalogo --> Explorar
    Empresa --> Radar["Radar<br/>comparar oportunidades"]
    Catalogo --> Radar
    Radar --> Projetos["Projetos<br/>escrever com evidências"]
    Catalogo --> Projetos
    Projetos --> Decisao["Proposta revisável<br/>decisão humana"]
```

**Stack:** Python 3.12 · FastAPI · LangGraph · Procrastinate · Next.js 14 · TypeScript · Supabase/Postgres · pgvector · OpenAI/Anthropic · Langfuse · Docker

## Como o sistema funciona

### 1. Descoberta e mapeamento

Cada fonte tem um adapter explícito. Agências conhecidas entram pelo pipeline versionado; achados da web ficam em staging até uma promoção humana. O resultado não é um grafo abstrato separado do produto: são tabelas gold relacionais que alimentam catálogo, exploração e matching.

```mermaid
flowchart LR
    Agencias["Agências conhecidas"] --> Adapters["Adapters por fonte"]
    Web["Descoberta web"] --> Staging["Staging"]
    Staging --> Gate{"Revisão humana"}
    Gate -->|promover| Bronze["Bronze imutável"]
    Adapters --> Bronze
    Bronze --> Silver["Silver estruturado"]
    Silver --> Gold["Gold<br/>entidades · relações · match_chunks"]
```

O gold é incremental: documentos inalterados são reconhecidos por `source_hash`. Vocabulários e regras de cada fonte vivem em documentação executável, lida pelo pipeline, para que uma mudança de domínio seja revisável como regra — não escondida em condicionais.

### 2. Matching explicável

O ranking usa a representação gold (`match_chunks`), independente do índice RAG empregado na escrita. Primeiro vêm vigência e restrições duras; somente os candidatos sobreviventes recebem afinidade. Um veredito LLM opcional atua no top-K, sem reordenar silenciosamente o universo de oportunidades.

```mermaid
flowchart LR
    Perfil["Perfil da empresa"] --> Chunks["Trechos da empresa"]
    Gold["Gold + match_chunks"] --> Vigencia["0 · vigência"]
    Vigencia --> Elegibilidade["1 · elegibilidade<br/>unknown nunca elimina"]
    Chunks --> Afinidade["2 · afinidade MaxSim<br/>sem LLM"]
    Elegibilidade --> Afinidade
    Afinidade --> Veredito["3 · veredito opcional<br/>somente top-K"]
    Veredito --> Resultado["Ranking + pares de evidência"]
```

Essa separação evita dois atalhos perigosos: confundir proximidade textual com elegibilidade e apresentar um número sem permitir que a pessoa confira por que ele apareceu.

### 3. Escrita assistida

O edital selecionado recebe chunking estrutural por artigo e parágrafo. Contexto de capítulo é incorporado antes do embedding; na consulta, busca lexical e densa são fundidas por RRF. O agente LangGraph trabalha sobre esse material com estado durável e ferramentas especializadas.

```mermaid
flowchart LR
    Edital["Edital selecionado"] --> Chunking["Chunking estrutural<br/>Art. · §"]
    Chunking --> Contexto["Contextual Retrieval"]
    Contexto --> Indice["pgvector + busca textual"]
    Pergunta["Brief e intenção"] --> Busca["HyDE + BM25 + dense<br/>fusão RRF"]
    Indice --> Busca
    Busca --> Agente["WritingSession<br/>LangGraph durável"]
    Agente --> Revisao["Critic + checklist<br/>compliance · qualidade · completude"]
    Revisao --> Rascunho["Rascunho com fontes<br/>revisão humana"]
```

## Decisões guiadas por evidência

| Decisão | Alternativa descartada | Evidência ou motivo |
| --- | --- | --- |
| **Representações específicas por tarefa** | Um único índice semântico para catálogo, match e escrita | Matching precisa de chunks comparáveis; escrita precisa preservar estrutura e contexto documental. Separar os índices torna o comportamento auditável. |
| **Elegibilidade antes de afinidade** | Deixar embeddings decidirem restrições duras | Uma empresa incompatível com região, porte ou prazo não deve subir no ranking. Dados desconhecidos pedem confirmação; nunca eliminam. |
| **Contexto incorporado aos chunks** | Embedding do trecho isolado | O bake-off do projeto elevou o MRR de **0,505 para 0,666** sobre chunks crus, refutando a hipótese de que apenas melhorar o parser seria suficiente. |
| **Descoberta web com gate humano** | Inserção automática no catálogo | Uma evidência encontrada não equivale a uma oportunidade válida. Promoção explícita impede que páginas incompletas contaminem o gold. |
| **Estado durável e isolamento adversarial** | Sessões em memória e testes apenas com mocks | Checkpoints ficam em Postgres; uma suíte de integração sobe Supabase real e tenta atravessar fronteiras de tenant nas superfícies críticas. |

Os detalhes e resultados que sustentam essas escolhas estão em [arquitetura](docs/architecture.md), [matching v3](docs/specs/v3-unified.md), [operações de avaliação](docs/specs/evaluation-operations.md) e [isolamento de tenants](docs/reference/tenant-isolation.md).

## Qualidade e avaliação

- **794 testes aprovados e 62 ignorados** no baseline atual, separados entre unitários e integração.
- CI bloqueante para **Ruff**, **pytest**, lint/build do frontend e suíte adversarial de isolamento com Supabase efêmero.
- Datasets golden, casos negativos difíceis e manifestos de execução tornam rodadas de avaliação comparáveis.
- `extraction` possui gate de qualidade aceito; as demais suítes geram diagnóstico e só se tornam bloqueantes após critérios explícitos.
- `mypy` permanece **consultivo**: a dívida de tipagem ainda é visível no CI, mas não é apresentada como garantia que o projeto não oferece.

O harness unificado cobre matching, RAG, escrita, extração, triagem, reranking e componentes auxiliares. Runs locais sempre registram um manifesto; publicação no Langfuse é opt-in.

Para reproduzir a avaliação hermética `e2e_health`, use o mesmo interpretador
que recebeu o extra de desenvolvimento e selecione explicitamente um perfil
local. A avaliação não usa banco, rede ou LLM reais, mas o harness reutilizado
depende de `pytest`:

```bash
.venv/bin/python -m pip install -e ".[dev]"
ENV_FILE=.env.staging-local ENVIRONMENT=test PYTHONPATH=src \
  .venv/bin/python -m radar.core.eval run e2e_health
```

Se o arquivo indicado por `ENV_FILE` não existir, o CLI encerra antes de
carregar o `.env` legado. Se `pytest` não estiver instalado no interpretador
ativo, o preflight informa o caminho exato para instalar o extra nesse mesmo
ambiente. Nunca execute essa avaliação com um perfil de produção ou staging.

## Rodar localmente

Pré-requisitos: Python 3.12, Node.js 20, Docker e Supabase CLI.

```bash
# dependências
pip install -e ".[dev]"
cd frontend && npm ci && cd ..

# banco e configuração local
supabase start
cp envs/.env.local.example .env.local
# preencha .env.local com os valores de `supabase status` e uma OPENAI_API_KEY

# aplique migrations e inicialize a sentinela (este reset apaga somente o banco local)
ENVIRONMENT=local DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  SUPABASE_URL=http://127.0.0.1:54321 python scripts/supabase_safe.py reset
ENV_FILE=.env.local python scripts/env_doctor.py
```

Em dois terminais:

```bash
ENV_FILE=.env.local uvicorn radar.api.app:app --reload --port 8000
```

```bash
cd frontend
# configure NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY e
# NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 em frontend/.env.local
npm run dev
```

O fluxo completo de bootstrap, pré-produção local e promoção segura está no [runbook de ambientes](docs/runbooks/environment-promotion.md). Para o worker:

```bash
ENV_FILE=.env.local python -m procrastinate --app=radar.core.tasks.app worker
```

## Estado e limitações

O repositório está em **pré-beta funcional**: pipeline, catálogo gold, matching, agentes e suíte de avaliação coexistem no mesmo produto, mas a operação pública ainda não é um SaaS disponível de ponta a ponta.

- A URL pública hoje é uma preview do frontend; ações dependentes da API falham enquanto o backend não estiver publicado.
- Cobertura e qualidade variam por agência: FINEP, FAPESP e FAPESC têm adapters próprios; descoberta web passa obrigatoriamente por revisão.
- Vereditos e texto gerado são apoio à análise, não parecer de elegibilidade nem garantia de aprovação.
- Capacidades experimentais ou dormentes — como extração automática de perfil, consulta CNPJ, embeddings locais e escrita automática de memória — permanecem desligadas até passarem por avaliação.
- A tipagem Python é gradual; `mypy` ainda não é gate de merge.

## Mapa do repositório

```text
src/radar/   API FastAPI · serviços · KG · retrieval · agentes · avaliação
frontend/    Next.js 14 · TypeScript · Tailwind · Radix UI
tests/       suítes unitárias e de integração
data/        bronze imutável · silver derivado · goldens e conteúdo de apoio
supabase/    migrations e configuração do ambiente local
docs/        arquitetura, domínio, specs, runbooks e referências
scripts/     operações administrativas e backfills idempotentes
```

Comece pelo [índice da documentação](docs/README.md). Para comandos de desenvolvimento e contratos do repositório, consulte [AGENTS.md](AGENTS.md).
