# Bake-off de modelos — LLM open/free-tier + embeddings

**Objetivo:** testar sistematicamente substitutos open-source / free-tier para os
pontos que consomem LLM (e embeddings), excluindo modelos **pagos da OpenAI**,
começando pelos caminhos de maior win provável. Gated por eval.

> **Handoff:** este doc é autocontido para retomar em outra conversa. Estado em
> 2026-06-15: levantamento de modelos feito (ver §Candidatos); nada testado ainda.

## Por que é tratável (não é exponencial)

Os tiers de consumo são **independentes** — cada um tem seu próprio gate de eval.
Otimiza-se cada tier contra sua suíte separadamente, depois compõem-se os
vencedores. Custo = **soma** dos testes por tier (~3-4 candidatos cada), não o
produto cartesiano.

## Instrumento de medição

`python -m core.eval <suite> --no-push` (grava em `eval_results/*.json`).
**Sempre rodar o baseline primeiro** (config atual) e guardar os números — toda
comparação é contra ele.

Mapa consumo → suíte que gateia:
- embeddings + rerank + contextual → **rag** (Recall@K, MRR, faithfulness)
- match Stage 2 + KG → **matching**
- extração (discovery/ETL/structurer) → **extraction**
- writing agent (+ critic) → **writing**
- triagem/enrich → sem suíte dedicada (baixo stake; olho + amostra)

## Como cada provider pluga

O sistema é **OpenAI-compatible** (`core/llm/llm_client.py make_client` aceita
`base_url`). Os seams já existem em `_make_client` de vários módulos:
`LLM_BACKEND=gemini` e `LLM_BACKEND=ollama` já mapeados. Um "combo" = env
(`base_url` + key + nome do modelo), salvo o tier que tem env próprio.

Slots de env por tier:
- barato/reasoning geral: `OPENAI_MODEL` (+ `LLM_BACKEND`)
- "pro" (extract/critic): `OPENAI_MODEL_PRO`, `OPENAI_MODEL_CRITIC`
- agêntico: `ANTHROPIC_MODEL_AGENT` / provider via `resolve_agent_provider`, ou o fallback OpenAI
- embeddings: **hardcoded** em `core/retrieval/embedder.py` → **precisa parametrizar antes** (ver pré-req)

## Candidatos por slot (excl. pago OpenAI)

**LLM free-tier (OpenAI-compat, viáveis p/ eval em lote):**
- **Gemini Flash-Lite / Flash** (AI Studio free): 1M TPM, 1500 RPD — único free que aguenta golden. `base_url=https://generativelanguage.googleapis.com/v1beta/openai/`
- **Cerebras** (free): rápido, **cap 8K contexto** no free (limita doc longo)
- **Groq / SambaNova** (free): rate-limit baixo → só smoke, não golden cheio

**LLM open-weight (hospedado barato OU self-host):**
- **DeepSeek V3.2** (MIT, OpenAI-compat) — reasoning
- **Qwen3.5-397B-A17B** (Apache, BFCL 0.729 = melhor open tool-calling) — agêntico/reasoning
- **Qwen3.5-9B / Llama 4 Scout** — barato/volume

**Embeddings (self-host Ollama / free API):**
- **Qwen3-Embedding-0.6B** (Apache, MTEB ~68 > atual 64.6, roda CPU/Ollama, dim 1024)
- **BGE-M3** (MIT, dim 1024, dense+sparse — casaria com o hybrid)
- **Qwen3-Embedding-8B** (MMTEB #1 70.58, dim 4096→MRL, exige GPU)
- baseline: text-embedding-3-large (dim 1536)

## Ordem de teste (maior win ÷ menor risco primeiro)

### 1. 🟢 Embeddings — `rag` suite
Maior win provável (coração do RAG; Qwen3-0.6B já mede acima no MTEB) e **isolado**.
- **Pré-req (bloqueante):** parametrizar `embedder.py` (modelo/base_url por env) — hoje hardcoded.
- **Pré-req 2 (dimensão):** a coluna `edital_chunks.embedding` é `vector(1536)`. Qwen3-0.6B/BGE-M3 são **1024** → mismatch. Não dá pra reusar a coluna. Opções: (a) eval **offline** em numpy sobre o corpus golden (cosseno, mede só o braço dense — decide o teto barato); (b) coluna-sombra `embedding_<modelo>` com a dim certa p/ eval end-to-end (RRF+rerank).
- **Gate:** `core.eval rag` — Recall@K/MRR ≥ baseline. **Não re-indexar prod até ganhar.**

#### Critério de promoção do filtro offline (revisado 2026-06-16)

O filtro offline mede o **dense isolado**, mas o arranjo de prod é híbrido: FTS
(léxico, model-agnostic) + RRF (k=60, por rank, scale-free) + **reranker**
(cross-encoder sobre um pool top-`k_candidates=20`, model-independent). Só **um**
knob é co-adaptado ao baseline OpenAI: `fts_weight=0.3` → o dense pesa **0.7**
([retriever.py](../../core/retrieval/retriever.py), calibrado no corpus FINEP
assumindo dense forte). Consequência: o dense-only é **conservador e pode dar
falso-negativo** — um candidato levemente abaixo ainda pode ganhar end-to-end se
(a) colocar o chunk certo no **pool top-20** (o reranker conserta a ordem) e/ou
(b) for socorrido por `fts_weight` maior. Logo "perdeu no dense isolado" **não
implica** "perde em produção".

Por isso medir o candidato em **dois `--top-k`**: `5` (qualidade final) **e `20`**
(contenção no pool do reranker). Três baldes:

| Resultado dense-only | Decisão |
|---|---|
| **WIN** — ≥ baseline em gold_recall@5 & recall@5 & MRR | promove ao `core.eval rag` (coluna-sombra), prioridade alta |
| **CLOSE** — abaixo no top-5 mas dentro do ruído (~≤0.05 em gold_recall@5) **ou** empata baseline em recall@20 / gold_recall@20 | **NÃO rejeita** — promove ao end-to-end varrendo `fts_weight`; rerank+fusão podem fechar o gap |
| **REJECT** — claramente abaixo **mesmo em k=20** (chunk certo não entra no pool) | descarta — o reranker não tem o que salvar (caso Qwen3-0.6B) |

**Caveat de amostra:** golden = 28 queries → gaps sub-0.05 são ruído, não sinal.
Re-tunar várias peças do arranjo por candidato (top-N do rerank, chunk size, k do
RRF) é overfit + combinatória → **fora do tier-1**; só `fts_weight` (1 knob) entra
na varredura end-to-end. O esparso/ColBERT do BGE-M3 é **outro track** (trocar o
braço léxico), não escolha de embedding.

### 2. 🟢 Tier barato — `rag` (rerank/contextual) + olho na triagem
- Slot: `OPENAI_MODEL` (+ `LLM_BACKEND=gemini`).
- Candidatos: Gemini Flash-Lite (free), Gemini Flash (free).
- Dado é **edital público** → free tier OK (sem questão de privacidade).

### 3. 🟡 Raciocínio — `matching` suite
- Slot: `OPENAI_MODEL` / `OPENAI_MODEL_PRO` (+ base_url).
- Candidatos: DeepSeek V3.2, Qwen3.5-397B, Gemini Flash (free).
- **Gate:** `core.eval matching`.

### 4. 🟡 Extração — `extraction` suite
- Slot: `OPENAI_MODEL_PRO` (role extract).
- Candidatos: DeepSeek V3.2, Qwen3.5, Gemini Flash.
- **Gate:** `core.eval extraction`.

### 5. 🔴 Agêntico — `writing` suite (POR ÚLTIMO)
- Slot: `ANTHROPIC_MODEL_AGENT` / provider, ou fallback OpenAI.
- Candidatos: Qwen3.5-397B (melhor open tool-calling), DeepSeek V4.
- **Gate:** `core.eval writing` **+ gate de grounding**.
- **Bloqueios:** (a) tool-calling open é frágil em loop — o risco real; (b) o gate de grounding **ainda não é detector confiável** (BACKLOG) → domar antes; (c) writing/profile = **dado de cliente** → proibido free-tier-com-treino (usar provider ZDR/pago).

## Mecânica de um combo

1. Set env (model + base_url + key) ou `LLM_BACKEND`.
2. `python -m core.eval <suite> --no-push --limit N` (smoke) → depois full.
3. Comparar `aggregate` vs baseline salvo em `eval_results/`.
4. Promove se ≥ baseline **e** custo/latência aceitáveis.

## Pré-requisitos de código (uma vez, antes de começar)

1. **`embedder.py`** — parametrizar modelo + base_url por env (hoje `EMBEDDING_MODEL` hardcoded). **Bloqueia o teste 1.**
2. **Dimensão** — decidir offline-numpy (rápido, braço dense) vs coluna-sombra (end-to-end). Recomendado: offline primeiro (filtro barato), coluna-sombra só p/ o candidato que passar.
3. **Keys no `.env`** — `GEMINI_API_KEY`, Groq/DeepSeek/OpenRouter conforme candidato; Ollama local p/ embeddings.
4. Confirmar que cada role-slot lê env separável (maioria já lê via `_make_client`).

## Caveats que decidem

- **Free tier ≠ produção:** rate-limit arrasta eval em lote — só Gemini Flash (1500 RPD) e Cerebras (1M TPD) aguentam o golden; Groq/Mistral só smoke.
- **Privacidade:** Gemini **AI Studio free** pode revisar prompt p/ treino → OK p/ edital público (tiers 1-4 sobre editais), **proibido** p/ writing/profile (dado de cliente).
- **Re-index de embeddings** é o único passo caro/irreversível → estritamente gated.
- **Tool-calling** é o teto do open no tier agêntico — esperar regressão lá, não nos tiers determinísticos.

## Resumo de uma linha

Teste por tier (independentes, gated por suíte), na ordem
**embeddings → barato → raciocínio → extração → agêntico**; promova só o que
bate o baseline; deixe o agêntico por último (tool-calling frágil + dado de
cliente + gate de grounding ainda não confiável).
