# Arquitetura — Radar de Editais

> **Autoridade:** runtime e fluxos implementados. Para regras de domínio, use
> [`WIKI.md`](../WIKI.md); para o mapa completo, use o
> [índice da documentação](README.md).

O produto conecta empresas early-stage/PME a oportunidades de fomento público
brasileiro através de três capacidades: **Explorar** (mapeamento do ecossistema
por catálogo e conversa), **Radar** (match empresa↔oportunidade) e **Projetos**
(propostas e pitches com RAG). **Descoberta** é a torneira operacional de novas
oportunidades com gate humano, não uma quarta jornada do usuário de produto.

Arquitetura v3 (concluída 2026-07-11): representações especializadas por
funcionalidade derivadas da mesma fonte — filtros estruturados + embeddings de
texto real para o match, tabelas relacionais para navegação, chunks contextuais
para escrita. Spec: [`docs/specs/v3-unified.md`](specs/v3-unified.md).

---

## 0. Deploy

Backend em Docker no host do operador, exposto via Cloudflare Tunnel (sem porta
aberta); frontend na Vercel; dados no Supabase Cloud.

```mermaid
flowchart LR
  subgraph HOST["Docker Compose (host)"]
    APP["app · API"]
    WORKER["worker · jobs assíncronos<br/>+ crons diários (ETL 03h · Descoberta 04h UTC)"]
    TUN["tunnel · cloudflared"]
  end
  TUN --> APP
  CF["Cloudflare Tunnel"] --> TUN
  VERCEL["Vercel · frontend Next.js"] --> CF
  APP --> SB[("Supabase Cloud<br/>Postgres + pgvector + Auth")]
  WORKER --> SB
  USER["Browser"] --> VERCEL
```

---

## 1. Plano de dados — das fontes ao catálogo de conhecimento

Fontes fixas do pré-beta: editais (FINEP, FAPESP, FAPESC, web), 90 ICTs
EMBRAPII, 17 investidores e 10 programas curados à mão (versionados no repo).

```mermaid
flowchart TB
  subgraph FONTES["Fontes"]
    AG["Agências (FINEP/FAPESP/FAPESC)<br/>scrapers diários"]
    WEB["Web · Descoberta<br/>busca + adapters"]
    CUR["Curadoria versionada<br/>investidores · programas · ICTs"]
  end

  WEB --> EVID["Evidências canônicas<br/>Crawl4AI opcional no worker"]
  EVID --> GATE["Staging + gate admin<br/>(promote/reject)"]
  GATE -->|promote| BRONZE["Bronze web<br/>versão aprovada"]
  BRONZE --> SILVER
  AG --> SILVER["Silver — transcrição estrutural<br/>verbatim, por seção (LLM leve por página)"]

  SILVER --> INGEST["Ingestão gold (incremental, diária)<br/>· metadados determinísticos<br/>· tagger LLM: setores (16) + tags de tecnologia<br/>· extração de elegibilidade (constraints + exclusões + público-alvo)<br/>· embeddings da entidade e dos trechos de match"]
  CUR --> INGEST

  INGEST --> KG[("Catálogo de conhecimento (Postgres)<br/>entidades · relações · trechos de match")]

  SILVER -.->|"sob demanda, quando o usuário<br/>engaja com um edital"| RAGCHUNKS[("Chunks de escrita<br/>contextuais + busca híbrida")]
```

**Entidades** (5 tipos): edital, programa, investidor, ICT, agência — com
setores (taxonomia fechada de 16), tags de tecnologia (folksonomia
normalizada), metadados de vigência/ticket e constraints de elegibilidade.
**Relações** (4, determinísticas): operado_por, subordinado_a,
exige_parceria_com, credenciada_por. Relações semânticas emergem em tempo de
consulta (tags compartilhadas + busca vetorial), não são mantidas como arestas.

LLM aparece em **dois pontos** do plano de dados (tagger + extração de
elegibilidade, ambos no ingest) — todo o resto é determinístico e
re-executável.

---

## 2. Radar — funil de match em 4 estágios

Match sobre **texto real** (trechos da empresa × trechos da oportunidade),
nunca sobre conceitos abstratos extraídos.

```mermaid
flowchart TB
  P["Perfil da empresa + documentos da library<br/>(chunkados por workspace; HyDE expande<br/>perfis ralos no cold start)"]
  P --> S0["Stage 0 — Vivo<br/>prazo futuro ou fluxo contínuo (determinístico)"]
  S0 --> S1["Stage 1 — Elegibilidade dura<br/>constraints × perfil · inelegível ELIMINA<br/>desconhecido NUNCA elimina"]
  S1 --> S2["Stage 2 — Afinidade semântica<br/>melhor pareamento por trecho da empresa<br/>(sum-of-max) + boost de setores · sem LLM"]
  S2 --> S3["Stage 3 — Precisão (top 5-10)<br/>veredito LLM lendo os trechos pareados<br/>+ ficha da oportunidade (async, cacheado)"]
  S3 --> CARDS["Cards com explicação por trecho real<br/>(matched_excerpts) + trilha investidor<br/>(tese × perfil, gate de estágio/setor)"]
```

Qualidade medida por gate absoluto (golden + hard negatives de elegibilidade);
parâmetros calibrados por bake-off: embeddings contextuais dos trechos de match
(venceram o cru por medição), agregação sum-of-max, boost de setores.

---

## 3. Escrita assistida

Sessão de escrita conversacional sobre um edital: primeiro turno gera o
rascunho completo (batch de seções), turnos seguintes iteram com o usuário.

- **RAG sobre o edital**: busca híbrida (densa + BM25 + rerank) nos chunks
  contextuais, criados sob demanda no primeiro engajamento.
- **Ficha da oportunidade** no contexto do agente: prazos, valores,
  elegibilidade, exclusões e público-alvo vindos do catálogo.
- **Guardrails**: Critic (subagente) + classificador de escopo antes de
  persistir rascunho; checklist de 3 passes paralelos (compliance, qualidade,
  completude) em background.
- **Playbooks por mecanismo** (subvenção, equity) guiam a estratégia do texto.

---

## 4. Mapeamento do ecossistema

Duas superfícies sobre o mesmo catálogo:

- **Catálogo navegável**: oportunidades, programas, investidores e ICTs com
  facetas por setor/tag, ficha por entidade e ofertas de investimento por fundo.
- **Exploração conversacional (ExploreAgent)**: agente com ferramentas de
  busca semântica de entidades ("quem atua em visão computacional?"), vizinhança
  estrutural (BFS nas relações), entidades relacionadas por tags compartilhadas,
  e o match como ferramenta. Respostas alimentam o perfil da empresa via
  diff sugerido (aceito pelo usuário — "AI drafts, humans decide").

---

## 5. Descoberta

Torneira de novas oportunidades da web (DOU e afins) → triagem → pacote
canônico de evidências → **staging com gate humano** (admin promove ou rejeita).
O pacote preserva a proveniência da página, de documentos oficiais e de adapters;
o Crawl4AI é um enriquecimento opcional, executado apenas no worker, para cobrir
conteúdo dinâmico, links e lacunas de evidência. Ele não substitui scrapers ou
adapters dedicados.

O `promote` materializa a versão aprovada em bronze `web`, de forma auditável, e
reutiliza o `WebScraper` para o caminho silver → ingest do plano de dados. A
oportunidade então entra no catálogo, no match e no RAG como qualquer edital de
agência. Descoberta nunca escreve diretamente no catálogo; falhas no
enriquecimento permanecem isoladas no staging e não publicam conteúdo pendente.

---

## 6. Runtime agêntico e memória

Todos os agentes (escrita, explore, critic) rodam num único runtime LangGraph:

- **Grafo ReAct** (agent → tools → memória → reflect) com checkpointer
  Postgres durável — sessões sobrevivem a restart.
- **Human-in-the-loop** nativo via interrupt.
- **Memória cross-session** por workspace (Store semântico): reflexões e
  padrões sintetizados em background.
- **Isolamento multi-tenant** por workspace em todas as superfícies de dado do
  usuário (RLS + namespaces), coberto por leak-tests com Postgres real.

Cinco tiers de LLM trocáveis por env var (embeddings, contextual, extração
determinística, explore, escrita) — trocar um não afeta os outros.

---

## 7. Avaliação

Harness unificado com suítes por funcionalidade (matching, RAG, escrita, entre
outras). `run` produz diagnóstico local por padrão; `--publish` envia uma
rodada completa ao Langfuse. `gate` aplica critérios versionados, rejeita
subconjuntos e distingue reprovação de qualidade, erro operacional e skip. Cada
JSON inclui manifesto de commit, datasets, modelos, configuração e
comparabilidade. Regra do projeto: gates medem **correção absoluta**, nunca
paridade com arquiteturas anteriores.

`extraction` é gate ativo. `matching` permanece candidato: os pisos de MRR,
recall e hard negatives estão declarados, mas não bloqueiam até que o limite de
`noise@8` seja aprovado e o contrato seja promovido explicitamente.
