# RADAR — Análise Arquitetural e Roadmap

> Comparação com Grantable · Vocabulário CoALA · Insights de Generative Agents  
> Data: 2026-05-11  
> Premissas: SaaS multi-empresa · FINEP como fonte inicial · Sistema reativo · Feedback loop prioritário · Brief de viabilidade como feature futura

---

## Sumário

1. [Comparação Radar × Grantable](#1-comparação-radar-×-grantable)
2. [Estado atual no vocabulário CoALA](#2-estado-atual-no-vocabulário-coala)
3. [Gaps arquiteturais (CoALA + Generative Agents)](#3-gaps-arquiteturais-coala--generative-agents)
4. [Insights e recomendações de desenvolvimento](#4-insights-e-recomendações-de-desenvolvimento)
5. [Recomendação de arquitetura](#5-recomendação-de-arquitetura)

---

## 1. Comparação Radar × Grantable

### 1.1 Legenda

| Símbolo | Significado |
|---|---|
| ✅ | Implementado e funcional |
| 🟡 | Parcialmente implementado ou com gap relevante |
| 🔧 | Fácil de adicionar (< 2 semanas, sem redesign) |
| 🏗️ | Requer desenvolvimento significativo (nova feature) |
| ➖ | Fora de escopo (deliberadamente) |

---

### 1.2 Tabela de comparação por capability

| Capability (Grantable) | Equivalente no Radar | Status | Arquivo(s) |
|---|---|---|---|
| **Organizational Memory** — contexto persistente acumulado por organização | `CompanyProfile` (perfil estruturado) + `ContentLibrary` (Supabase) | 🟡 Exists mas sem acumulação temporal de outcomes | `domain/user_profile.py`, `core/content_library.py` |
| **`/profile`** — construção de perfil da organização via URL ou entrevista | `ProfileExtractor` (extração por URL via LLM) | ✅ | `core/ingestion/profile_extractor.py` |
| **`/boilerplate`** — biblioteca de conteúdo reutilizável | `ContentLibrary` com enrichment LLM (summary, key_facts, themes) | ✅ | `core/content_library.py`, `backend/library_routes.py` |
| **`/grant-writing`** — drafting estruturado em 5 fases | `WritingSession` (multi-turn, prompt caching, history compression) + `ProposalDrafter` (one-shot) | 🟡 Falta estrutura formal de fases e outline de compliance | `core/writing_session.py`, `agents/writer_agent.py` |
| **`/prospecting`** — pesquisa e scoring de financiadores | `HybridMatchService` (Stage 1 determinístico + Stage 2 LLM) e `KGMatchService` | ✅ Para FINEP. Sem base de 800k financiadores (fora de escopo) | `core/hybrid_match_service.py`, `core/kg_match_service.py` |
| **`/review`** — revisão em 3 passes (compliance, qualidade, completude) | `ChecklistService` + `auto_review_checklist` (1 passo) | 🟡 1 passe vs. 3 passes; falta avaliação de qualidade narrativa | `core/checklist_service.py` |
| **`/archive`** — limpeza de workspace | Não existe | 🔧 Operação de soft-delete sobre `content_items` existente | — |
| **Grant Opportunity Brief** — decision matrix 0-100 antes de aplicar | Não existe (matching retorna score, não brief formal) | 🏗️ Feature futura | — |
| **Slash commands / Skill picker** | Não existe interface de skills | 🔧 Camada de UI sobre serviços existentes | — |
| **@ mentions de arquivos** | Não existe; `library_items` passados por ID na API | 🔧 Adicionar resolução de @ no frontend + `WritingSession` | — |
| **Model tiers (Auto/Pro/Fast)** | LLM configurável via env var (OpenAI/Gemini/Ollama) | 🟡 Funcional mas sem escolha por mensagem | `core/kg_match_service.py`, `core/writing_session.py` |
| **Multi-tenancy / Workspaces** | Supabase workspaces por `user_id` | 🟡 Estrutura existe; profiles não persistem em DB (JSON em disco) | `core/content_library.py`, `core/infra/db.py` |
| **Feedback loop / aprendizado de outcomes** | Não existe | 🏗️ Prioritário segundo objetivo do produto | — |
| **Funder database (externo)** | Apenas FINEP (knowledge graph local) | ➖ Fora de escopo intencional | `pipeline/build_knowledge_graph.py` |
| **Criação de arquivos no workspace** | Não existe; outputs são respostas de API | 🏗️ Mudança de paradigma (doc viewer + file tree) | — |

---

### 1.3 Síntese visual

```
                        GRANTABLE
                            │
    ┌───────────────────────┼───────────────────────┐
    │                       │                       │
  ✅ JÁ EXISTE           🟡 PARCIAL              🏗️ FALTA
  ─────────────          ──────────              ─────────
  /profile               /grant-writing          Grant Opportunity
  /boilerplate           /review (1-pass)        Brief (decision matrix)
  /prospecting           Organizational          Feedback loop /
  (FINEP-only)           Memory (sem             aprendizado de outcomes
  Workspace              outcomes)               File tree / doc viewer
  (Supabase)             Model tiers             Slash command UI
                         (sem escolha            Reflection module
                          por mensagem)
```

---

## 2. Estado atual no vocabulário CoALA

CoALA (Cognitive Architectures for Language Agents) decompõe agentes em três dimensões: **Memória**, **Espaço de Ações** e **Tomada de Decisão**. O radar_editais já implementa boa parte dessas dimensões, mas com lacunas importantes.

---

### 2.1 Memória

| Tipo CoALA | O que é | Implementação no Radar | Arquivo | Lacunas |
|---|---|---|---|---|
| **Memória de Trabalho** | Variáveis ativas na sessão: percepções, metas, contexto recente | `CompanyProfile.to_context()` + `WritingSession._history` (últimos 6 turnos) + `_history_summary` (comprimido) | `writing_session.py` | Não persiste entre sessões; cada `WritingSession` começa do zero |
| **Memória Episódica** | Log de eventos e experiências passadas da organização | `ContentLibrary` (Supabase: propostas, projetos, key_facts) | `content_library.py` | **Sem ApplicationLog**: outcomes de aplicações (aprovado/reprovado) não são registrados. Sem timestamps de eventos sequenciais |
| **Memória Semântica** | Conhecimento estruturado sobre o mundo externo | Knowledge Graph (`index.json` + wiki pages `.json`) — editais FINEP normalizados com schemas autoritativos | `pipeline/build_knowledge_graph.py`, `core/wiki_schema.py` | Completa para FINEP. Limitada a dados de editais; sem conhecimento sobre padrões de aprovação histórica |
| **Memória Procedural** | Heurísticas e regras que guiam comportamento | `HybridMatchService` scoring rules (`_WEIGHTS`, `_ENTITY_MAP`, `_MECHANISM_MAP`), `wiki_schema.py` vocabulários, pipeline ETL | `hybrid_match_service.py`, `wiki_schema.py` | **Estática**: pesos e regras são constantes hardcoded. Não aprendem com outcomes |

**Diagrama da memória atual:**
```
┌─────────────────────────────────────────────────────────────┐
│                    MEMÓRIA DO RADAR                         │
│                                                             │
│  TRABALHO (volátil por sessão)                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ CompanyProfile.to_context()                          │  │
│  │ WritingSession._history (últimos 6 turnos)           │  │
│  │ WritingSession._history_summary (comprimido)         │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  EPISÓDICA (persistente — Supabase)                ⚠️GAP   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ContentLibrary: propostas, projetos, key_facts       │  │
│  │ [FALTA] ApplicationLog: outcomes de aplicações       │  │
│  │ [FALTA] SessionLog: histórico de sessões de escrita  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  SEMÂNTICA (persistente — disco)                   ✅OK    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ knowledge_graph/index.json (todos os editais FINEP)  │  │
│  │ knowledge_graph/wiki_pages/*.json (cards ricos)      │  │
│  │ knowledge_graph/index_historico.json                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  PROCEDURAL (hardcoded no código)                  ⚠️GAP   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ _WEIGHTS = {elegibilidade: 30, tematico: 25, ...}    │  │
│  │ _ENTITY_MAP, _PORTE_MAP, _MECHANISM_MAP              │  │
│  │ wiki_schema vocabulários                             │  │
│  │ [FALTA] Pesos atualizáveis por feedback de outcomes  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

### 2.2 Espaço de Ações

| Tipo CoALA | Subcategoria | Implementação no Radar | Arquivo |
|---|---|---|---|
| **Internas — Raciocínio** | Cadeia de pensamento, planejamento | `KGMatchService.match()` (LLM raciocina sobre índice), `WritingSession.turn()` (LLM raciocina sobre proposta) | `kg_match_service.py`, `writing_session.py` |
| **Internas — Recuperação** | Busca em memória | `HybridMatchService._get_editais_with_cards()`, `search_items()` (ilike no título), `_load_documents()` | `hybrid_match_service.py`, `content_library.py` |
| **Internas — Aprendizado** | Atualização de memória com nova informação | `enrich_content()` (extrai key_facts de docs), `build_knowledge_graph.py` (atualiza KG do bronze) | `content_library.py`, `pipeline/build_knowledge_graph.py` |
| **Externas — Diálogo** | Interação com o usuário | `WritingSession.turn()` (multi-turn chat), `get_section_starter()` | `writing_session.py` |
| **Externas — Digital** | Acesso a sistemas externos | `ProfileExtractor._fetch_url()` (web scraping), FINEP scrapers (`pipeline/extractors/finep.py`) | `profile_extractor.py`, `pipeline/extractors/` |
| **Externas — Físico** | Atuação no mundo físico | ➖ Fora de escopo | — |

**Lacunas no espaço de ações:**

| Ação CoALA | O que falta |
|---|---|
| **Recuperação multi-critério** | `search_items()` usa apenas `ilike`. Não há scoring por relevância, recência ou importância (como Generative Agents: `recency + importance + relevance`) |
| **Aprendizado de outcomes** | Não existe ação que processe um outcome (aprovado/reprovado) e atualize memória procedural ou episódica |
| **Reflexão** | Não existe ação que sintetize memórias episódicas em insights de nível superior ("esta empresa tem melhor fit com subvenção baseado em 3 matches anteriores aprovados") |

---

### 2.3 Tomada de Decisão

CoALA define 4 níveis de sofisticação:

| Nível | Descrição | Radar atual |
|---|---|---|
| **1 — Reativo simples** | Percepção → 1 chamada LLM → ação | `KGMatchService.match()`, `ProposalDrafter.draft_proposal()` |
| **2 — Multi-passo fixo** | Sequência determinística de chamadas | `HybridMatchService.match()` (Stage1 → Stage2), `WritingSession.turn()` (prefixo estático + histórico) |
| **3 — Planejamento com avaliação** | O agente propõe, avalia e seleciona ações | ❌ Não implementado |
| **4 — Meta-cognição** | O agente raciocina sobre seu próprio raciocínio | ❌ Não implementado |

O sistema opera nos **níveis 1 e 2**. Para o objetivo de feedback loop e futuramente o Grant Opportunity Brief, será necessário avançar para o **nível 3**.

---

## 3. Gaps arquiteturais (CoALA + Generative Agents)

### 3.1 Gap 1 — Memória Episódica de Outcomes (crítico)

**O que falta:**  
O sistema não registra o que aconteceu após um match ou uma sessão de escrita. Não sabe se a empresa aplicou, se foi aprovada, qual foi o feedback.

**Impacto:**  
Sem esse registro, não há como fechar o feedback loop. A memória procedural (pesos de matching) nunca melhora. O sistema trata a 50ª aplicação com a mesma ignorância da primeira.

**O que o Generative Agent faria:**  
Cada evento (match gerado, sessão iniciada, proposta submetida, outcome recebido) seria um objeto na **Memory Stream** com timestamp, `importance_score`, e embedding para recuperação futura.

**Solução para o Radar:**
```
Tabela: application_log
  - id, workspace_id, edital_id
  - session_id (FK → WritingSession)
  - match_score, match_dimensions (JSON)
  - status: "matched" | "brief_gerado" | "proposta_iniciada" | "submetida" | "aprovada" | "reprovada"
  - feedback_notas (texto livre)
  - created_at, updated_at
```

---

### 3.2 Gap 2 — Função de Recuperação Multi-critério (importante)

**O que falta:**  
`search_items()` usa `ilike` no título. A `WritingSession` injeta todos os `library_items` passados por parâmetro sem priorização.

**O que o Generative Agent faria:**  
Recuperação ponderada por três scores:
```
score_final = α·recency + β·importance + γ·relevance
```
- `recency`: decaimento exponencial por tempo desde criação
- `importance`: estimativa LLM no momento do upload (1-10)
- `relevance`: cosine similarity do embedding do item com a query atual

**Solução para o Radar:**  
Adicionar campo `importance_score` (int 1-10, gerado pelo `enrich_content`) na tabela `content_items`, e usar para priorizar quais itens injetar no contexto da `WritingSession` quando o total de itens exceder o limite de tokens.

---

### 3.3 Gap 3 — Módulo de Reflexão (importante para o feedback loop)

**O que falta:**  
Não existe nenhuma síntese de experiências passadas em insights de nível superior.

**O que o Generative Agent faria:**  
Trigger periódico (ex: após N novas entradas no `application_log`): gera perguntas sobre os padrões → busca evidências nas memórias → sintetiza reflexões que ficam armazenadas como memória semântica da organização.

**Solução para o Radar:**  
`ReflectionService` — serviço que roda periodicamente (ou on-demand) por workspace:
```python
# Pseudo-código
def reflect(workspace_id: str) -> list[str]:
    outcomes = load_application_log(workspace_id)
    questions = llm_generate_questions(outcomes)   # "Que padrões existem nos matches aprovados?"
    evidence = retrieve_relevant_memories(questions)
    insights = llm_synthesize(questions, evidence)
    save_as_semantic_memory(workspace_id, insights)
    return insights
```

Insights gerados alimentam a `WritingSession` como contexto adicional (ex: "Esta empresa tem 80% de aprovação em editais de subvenção não-reembolsável — priorize esse mecanismo").

---

### 3.4 Gap 4 — `/review` incompleto (médio prazo)

**O que falta:**  
`ChecklistService` faz 1 passe de compliance (requirements presentes/ausentes). Faltam:
- Passe 2: avaliação de **qualidade narrativa** (clareza, coerência, persuasão)
- Passe 3: **completude** (todas as seções obrigatórias preenchidas com profundidade adequada)

**Solução:** Estender `auto_review_checklist` em 3 chamadas LLM sequenciais com prompts distintos por passe.

---

### 3.5 Gap 5 — Compliance Verifier Paralelo à WritingSession (importante)

**O que falta:**  
O `ChecklistService` roda on-demand ao final de uma sessão — uma verificação post-hoc. Não existe nenhum mecanismo que observe os turnos da `WritingSession` em tempo real e sinalize violações de compliance conforme o usuário escreve.

**Impacto:**  
O usuário descobre problemas de aderência ao edital apenas ao solicitar revisão, depois de vários turnos de escrita. Retrabalho custoso; ausência de feedback formativo.

**Solução:**  
`ComplianceMonitor` — sub-agente especializado que roda **em paralelo** a cada turno da `WritingSession`:
```python
# Integração na WritingSession.turn()
async def turn(self, user_message: str) -> tuple[str, list[str]]:
    draft_response = await self._llm_turn(user_message)
    compliance_flags = await ComplianceMonitor.check_turn(
        draft=draft_response,
        edital_requirements=self._edital_requirements,
    )
    return draft_response, compliance_flags  # flags retornados ao frontend
```

O `ComplianceMonitor` usa um prompt enxuto (sem o histórico de turnos) focado apenas em checklist de requisitos mandatórios do edital. Custo baixo, latência paralela ao LLM principal.

---

### 3.6 Gap 6 — Grant Opportunity Brief (futuro)

**O que falta:**  
Avaliação formal antes de iniciar uma proposta. O `HybridMatchService` retorna um score de 0-10, mas não gera um documento de viabilidade com critérios explícitos e recomendação go/no-go.

**Solução:**  
`OpportunityBriefService` — evolução do HybridMatch Stage 1 para um documento estruturado:
```
Brief de Oportunidade (output)
  ├── Critérios mandatórios (pass/fail): elegibilidade, CNPJ, porte
  ├── Decision Matrix (0-100):
  │     ├── Alinhamento temático    (30 pts)
  │     ├── Elegibilidade formal    (25 pts)
  │     ├── TRL fit                 (20 pts)
  │     ├── Mecanismo financeiro    (15 pts)
  │     └── Contrapartida           (10 pts)
  ├── Avaliação narrativa (LLM)
  └── Recomendação: GO | GO_COM_RESSALVAS | NO_GO
```

---

### 3.7 Gap 7 — Taxonomia de Falhas no ETL (pipeline)

**O que falta:**  
Os scrapers e estágios ETL falham silenciosamente ou com erros genéricos. Não há distinção entre tipos de falha, nem lógica de retry diferenciada por causa.

**Impacto:**  
Falhas de rede (transitórias) são tratadas igual a falhas de schema (permanentes), resultando em retries inúteis ou dados corrompidos ingeridos sem sinalização.

**Solução:**  
Taxonomia de falha explícita para o pipeline:

| Categoria | Exemplos | Retry? |
|---|---|---|
| `timeout` | Request FINEP lento, PDF grande | Sim — backoff exponencial |
| `parse_error` | HTML mudou estrutura, JSON malformado | Não — requer intervenção manual |
| `schema_violation` | Edital fora do vocabulário autorizado | Não — entra em quarentena para revisão |
| `llm_refusal` | LLM recusou enriquecimento por conteúdo | Não — flag manual, skip com log |
| `duplicate` | Hash já existe no bronze | Não — skip silencioso |

Implementar como exceções tipadas em `pipeline/extractors/` e handler centralizado em `scripts/run_all.py` que persiste o log de falha no Supabase (tabela `pipeline_errors`).

---

## 4. Insights e recomendações de desenvolvimento

### 4.1 Sequência recomendada de implementação

```
FASE 1 — Fechar os fundamentos (1-2 meses)
  ├── RAG nos PDFs do edital (bge-m3 + chunking estrutural — ver §4.5)
  ├── ApplicationLog (Supabase): registrar eventos por workspace
  ├── importance_score em content_items (enrich_content)
  ├── /review em 3 passes (estender ChecklistService)
  └── @ mentions no frontend + WritingSession

FASE 2 — Feedback loop (2-3 meses)
  ├── ReflectionService: síntese de outcomes → insights semânticos
  ├── Recuperação multi-critério em ContentLibrary
  └── Insights de reflexão injetados na WritingSession

FASE 3 — Grant Opportunity Brief (3-4 meses)
  ├── OpportunityBriefService (evolução do HybridMatch)
  ├── Documento gerado no workspace (file tree / Supabase storage)
  └── Integração com ApplicationLog (GO → inicia sessão de escrita)

FASE 4 — Maturidade (roadmap)
  ├── Model tier selection por mensagem (Auto/Pro/Fast)
  ├── Slash command UI (/match, /brief, /draft, /review, /reflect)
  ├── Expansão para outras fontes (BNDES, FAPESP, CNPq)
  └── Skills por fonte: arquivos docs/playbooks/<fonte>_compliance.md carregados
      condicionalmente na WritingSession com base na fonte do edital,
      permitindo regras de aderência específicas sem reescrita de código
```

---

### 4.2 Decisões de design críticas

| Decisão | Opção A | Opção B | Recomendação |
|---|---|---|---|
| **Persistência de sessões** | Manter `WritingSession` em memória (atual) | Serializar sessão no Supabase entre requests | **Opção B** para SaaS multi-empresa — usuário precisa retomar sessões |
| **Reflexão: trigger** | On-demand (usuário solicita) | Assíncrono pós-outcome (background job) | **Ambos**: on-demand imediato, automático após N outcomes |
| **Pesos de matching** | Hardcoded (atual) | Atualizáveis por workspace via feedback | **Atualização global** primeiro (mais dados), depois por workspace |
| **Embedding para retrieval** | Sem embeddings (atual, keyword-only) | Embeddings por item em `content_items` | **Com embeddings** — necessário para retrieval semântico real |
| **File tree / doc viewer** | API retorna texto (atual) | Documentos persistem em Supabase Storage | **Supabase Storage** para paridade com Grantable |

---

### 4.3 Princípios do Generative Agent aplicáveis

| Princípio | Aplicação no Radar |
|---|---|
| **Memory Stream** | `application_log` + `content_items` como stream cronológico de eventos por workspace |
| **Importance scoring** | LLM atribui score 1-10 a cada ContentItem no upload; score decai com tempo para itens não referenciados |
| **Retrieval function** | `score = 0.4·recency + 0.3·importance + 0.3·cosine_similarity` ao recuperar contexto para WritingSession |
| **Reflection trigger** | Após cada 5 outcomes registrados no `application_log` (equiv. ao `Σimportance ≥ 150` do paper) |
| **Hierarquia de reflexão** | Nível 1: "Esta empresa perdeu os últimos 3 editais de TRL 4-6" → Nível 2: "O problema é posicionamento de maturidade, não alinhamento temático" |

---

### 4.4 Princípios do CoALA aplicáveis

| Princípio CoALA | Aplicação |
|---|---|
| **Separação de memórias** | Nunca misturar semântica (KG de editais) com episódica (histórico da empresa) no mesmo store |
| **Ações de aprendizado são ações** | `ReflectionService.reflect()` deve ser tratado como uma ação explícita do agente, não efeito colateral de outra ação |
| **Memória procedural deve ser editável** | `_WEIGHTS` do HybridMatch devem migrar para configuração por workspace (Supabase), não constantes de código |
| **Níveis de decisão explícitos** | Nomear e documentar qual nível cada serviço opera: `ProposalDrafter` = Nível 1, `HybridMatch` = Nível 2, `OpportunityBrief` = Nível 3 |

---

### 4.5 Configuração de Embeddings e RAG

#### Modelo de embedding: BAAI/bge-m3

**Justificativa da escolha** — análise de candidatos para deploy local gratuito:

| Modelo | Contexto | Dim | Português | Ollama | Veredicto |
|---|---|---|---|---|---|
| **BAAI/bge-m3** | 8.192 tokens | 1.024 | ✅ MIRACL + MLDR multilingual | ✅ `ollama pull bge-m3` (1,2 GB) | **Escolhido** |
| intfloat/multilingual-e5-large | 512 tokens | 1.024 | ✅ MTEB multilingual | ❌ Apenas sentence-transformers | Descartado (contexto curto) |
| nomic-embed-text | 2.048 tokens | 768 | ❌ Focado em inglês | ✅ Ollama (274 MB) | Descartado (sem pt) |
| neuralmind/BERTimbau-large | 512 tokens | 1.024 | ✅ PT nativo | ❌ Não é sentence-embedding | Descartado (MLM, não retrieval) |

**Por que bge-m3 vence para editais:**

1. **Retrieval híbrido nativo** — suporta simultâneos dense (semântico) + sparse (BM25-like) + ColBERT. Para editais, termos jurídicos exatos como "subvenção econômica", "CNPJ", "contrapartida" são capturados pelo sparse enquanto o dense captura intenção semântica. Os dois juntos superam qualquer modo isolado.

2. **Contexto de 8.192 tokens** — seções inteiras de editais FINEP (tipicamente 300-800 tokens) cabem em um único chunk sem truncamento.

3. **Disponível no Ollama com 1 comando** — sem dependências extras além do que já está no stack.

```bash
ollama pull bge-m3   # 1,2 GB, MIT license
```

```python
import ollama

def embed(texts: list[str]) -> list[list[float]]:
    response = ollama.embed(model="bge-m3", input=texts)
    return response["embeddings"]
```

---

#### Estratégia de chunking: Estrutural por seção

Editais FINEP são documentos legais hierárquicos com estrutura previsível:

```
Edital
├── Capítulo I — Disposições Gerais
│   ├── Art. 1º — Objeto
│   └── Art. 2º — Definições
├── Capítulo II — Das Condições de Participação
│   ├── Art. 3º — Público-Alvo / Elegibilidade
│   └── Art. 4º — Vedações
├── Capítulo III — Dos Recursos
│   ├── Art. 5º — Valor e Modalidade
│   └── Art. 6º — Contrapartida
...
```

**Estratégia escolhida: Document-Based Chunking com âncoras de seção**

Razão: preservar a coerência jurídica de cada artigo. Um chunk que mistura metade do Art. 5º com metade do Art. 6º pode confundir elegibilidade com contrapartida — duas dimensões críticas para o matching e a proposta.

**Parâmetros:**

| Parâmetro | Valor | Justificativa |
|---|---|---|
| Separadores primários | `\nArt.`, `\nArtigo`, `\nCapítulo`, `\nSeção`, `\n§` | Fronteiras naturais do edital |
| Separadores secundários | `\n\n`, `\n` | Fallback para parágrafos |
| Chunk-size alvo | **800 tokens** | Benchmark LlamaIndex: 1.024 tokens ótimo para docs técnicos; 800 deixa margem para overlap e metadados |
| Overlap | **150 tokens** | Evita perda de contexto nas bordas de artigos longos |
| Chunk mínimo | 80 tokens | Artigos curtos são merged com o seguinte |
| Chunk máximo | 1.500 tokens | Artigos muito longos são subdivididos em parágrafos |

**Metadados injetados em cada chunk** (recuperados junto com o texto):

```python
{
    "edital_id": "finep_2025_bioeconomia",
    "source_file": "edital_principal.pdf",
    "section_title": "Art. 3º — Condições de Elegibilidade",
    "chunk_index": 7,
    "page_range": "4-5",
}
```

Os metadados permitem ao LLM citar a seção exata e ao sistema filtrar chunks por seção relevante à pergunta (ex: só chunks de elegibilidade para a pergunta "minha empresa pode se candidatar?").

**Implementação no WritingSession:**

```
Atual (context stuffing):
  _documents_text = concat(todos os PDFs)
  → injetado integralmente em cada turno

Alvo (RAG):
  No __init__:
    chunks = chunk_edital(edital_id)          # segmenta e persiste
    embeddings = embed([c.text for c in chunks])  # bge-m3 via Ollama

  No turn():
    query_embedding = embed([user_message])
    top_k = retrieve(query_embedding, edital_id, k=5)  # hybrid: dense + sparse
    context = format_chunks(top_k)            # inclui metadados de seção
    → injeta apenas os 5 chunks mais relevantes (~4.000 tokens vs. 40.000)
```

**Redução de custo estimada:** de ~40.000 tokens/turno (context stuffing) para ~5.000 tokens/turno (RAG com k=5). Redução de 87% no custo de tokens por sessão, sem perda de qualidade para perguntas específicas.

**Store dos chunks:** Supabase com pgvector. Schema:

```sql
CREATE TABLE edital_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    edital_id   TEXT NOT NULL,
    chunk_index INT  NOT NULL,
    text        TEXT NOT NULL,
    section     TEXT,                      -- título da seção
    source_file TEXT,
    page_range  TEXT,
    embedding   VECTOR(1024),              -- bge-m3 dense (1024 dims)
    sparse_vec  JSONB,                     -- bge-m3 sparse (token weights)
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON edital_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

**Nota sobre o sparse vector:** o bge-m3 retorna pesos por token (BM25-like) como dict `{token_id: weight}`. Armazenar em JSONB permite score léxico via `pg_trgm` ou reranking pós-retrieval. Para simplificar na Fase 1, pode-se iniciar só com dense e adicionar hybrid na Fase 2.

---

## 5. Recomendação de arquitetura

### 5.1 Arquitetura alvo (CoALA com Reflexão do Generative Agent)

```
┌─────────────────────────────────────────────────────────────────┐
│                    RADAR — ARQUITETURA ALVO                     │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                  MEMÓRIA DE TRABALHO                    │   │
│  │  CompanyProfile · WritingSession._history · Outline     │   │
│  │  Insights de Reflexão (injetados por sessão)            │   │
│  └───────────────────────┬─────────────────────────────────┘   │
│                           │ ler/escrever                        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              MEMÓRIAS DE LONGO PRAZO (Supabase)         │   │
│  │  ┌────────────────┐ ┌─────────────────┐ ┌───────────┐  │   │
│  │  │   Episódica    │ │    Semântica     │ │Procedural │  │   │
│  │  │                │ │                 │ │           │  │   │
│  │  │ content_items  │ │ KG FINEP        │ │ _WEIGHTS  │  │   │
│  │  │ application_   │ │ (index.json +   │ │ por       │  │   │
│  │  │ log ← [NOVO]   │ │  wiki_pages)    │ │ workspace │  │   │
│  │  │ reflection_    │ │ Reflexões       │ │ ← [NOVO]  │  │   │
│  │  │ insights←[NOVO]│ │ ← [NOVO]        │ │           │  │   │
│  │  └────────────────┘ └─────────────────┘ └───────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   ESPAÇO DE AÇÕES                       │   │
│  │  Raciocínio: HybridMatch · KGMatch · WritingSession     │   │
│  │  Recuperação: search_items (multi-critério) ← [UPGRADE] │   │
│  │  Aprendizado: enrich_content · ReflectionService ←[NOVO]│   │
│  │  Diálogo: WritingSession.turn · ProfileExtractor        │   │
│  │  Digital: FINEP scrapers · PDF extraction               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                 TOMADA DE DECISÃO                       │   │
│  │  Nível 1: ProposalDrafter (one-shot)                    │   │
│  │  Nível 2: HybridMatch (Stage1→Stage2), WritingSession   │   │
│  │  Nível 3: OpportunityBrief ← [FUTURO]                   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

### 5.2 Justificativa da escolha arquitetural

**Por que CoALA como espinha dorsal:**
- O sistema já implementa os três módulos de CoALA (memória, ações, decisão) em estado funcional. CoALA fornece o vocabulário para identificar gaps sem exigir reescrita.
- A separação explícita de tipos de memória (episódica ≠ semântica ≠ procedural) evita o erro mais comum em sistemas de IA: colocar tudo em "contexto do prompt".
- CoALA mapeia diretamente para o problema de SaaS multi-empresa: cada workspace tem seu próprio estado de memória isolado.

**Por que incorporar Reflexão do Generative Agent:**
- O requisito de feedback loop (outcomes → melhoria do sistema) é exatamente o papel do módulo de Reflexão do paper de Park et al.
- A hierarquia de reflexão (observação → insight de 1ª ordem → insight de 2ª ordem) é o mecanismo correto para transformar "esta empresa perdeu 3 grants" em "o problema é TRL mismatch, não alinhamento temático".
- É implementável de forma incremental sobre a estrutura atual — não exige redesign.

**O que NÃO adotar (Generative Agents):**
- Memory Stream completo (log de cada evento com embedding): custo alto, ganho marginal para o caso de uso. Usar `application_log` tabular é suficiente para o feedback loop.
- Planejamento hierárquico autônomo (Smallville-style): o sistema permanece reativo por decisão de produto.
