# Perfil demo/custo — trocar qualidade por custo, só onde o eval verifica

**Objetivo:** rodar a versão demo mais barata possível **sem degradar às cegas**.
A premissa do bake-off ([llm-embedding-bakeoff.md](embedding-bakeoff.md)) vale aqui:
cada ponto de LLM/embedding é trocável por env; **só promovemos um modelo mais barato
onde uma suíte de eval confirma que ele não piora** (≥ baseline). Onde o gate não é
confiável ou não rodou, fica no **modelo confiável** — e isso é registrado como
limitação, não escondido.

## Princípios (o que decide cada linha)

1. **Cortar custo só com gate verde.** Sem eval que confirme paridade, não desce.
2. **Dado de cliente nunca em free-tier-com-treino.** Writing/profile (tier 5) é dado
   de cliente → proibido Gemini AI Studio free e afins; só provider ZDR/pago. Editais
   (tiers 1-4) são públicos → free-tier OK.
3. **Default inalterado é o estado seguro.** Cada parametrização preserva o
   comportamento atual sem env setada; o perfil abaixo é opt-in.

## Dashboard de custo (estado em 2026-06-16)

| Ponto / tier | Env knob | Gate | Veredito | Valor na demo |
|---|---|---|---|---|
| **Embeddings** (t1) | `EMBEDDING_MODEL` | `rag` offline | baseline OpenAI **vence** (Qwen3-0.6B/BGE-M3 reprovam) | **mantém** `text-embedding-3-large` |
| **Contextualização** (t2) | `CONTEXTUAL_RETRIEVAL_MODEL` (+`_BASE_URL`/`_API_KEY`) | `rag` offline | Gemini Flash-Lite **empata** com gpt-4o-mini | **troca → Gemini Flash-Lite** (free, editais públicos) |
| Contextualização — desligar | `CONTEXTUAL_RETRIEVAL=false` | parcial | cru ≈ contexto no offline; **não** confirmado e2e | candidato — precisa `core.eval rag` e2e antes |
| **Matching** (t3) | `LLM_BACKEND`/`GEMINI_MODEL` | `matching` | **inconclusivo** (golden N=2 + Gemini trunca por thinking-tokens) | **mantém** OpenAI |
| **Extração** (t4) | `LLM_BACKEND`/`GEMINI_MODEL` (slot `OPENAI_MODEL_PRO`) | `extraction` | Gemini **não rodou** (quota free diária estourou) | **mantém** `gpt-4o` — destravar com chave paga |
| **Writing + critic** (t5) | `ANTHROPIC_MODEL_AGENT`, `OPENAI_MODEL_CRITIC` (+ base_url, ver abaixo) | `writing`+grounding | gate de grounding **não confiável** (BACKLOG) + dado de cliente | **mantém** modelo confiável — não promover |
| Triagem/enrich | `OPENAI_MODEL` | sem suíte (olho/amostra) | baixo stake | já barato (`gpt-4o-mini`); manter |

**Resumo honesto:** hoje o único corte de custo **verificado por eval** é a
contextualização (tier 2). Os demais ficam no modelo confiável até o gate respectivo
amadurecer — essa é a **limitação atual** do sistema: sabemos cortar custo onde
medimos; no matching/extração/agêntico ainda não medimos de forma conclusiva.

## Bloco `.env` da demo (copiável)

```bash
# ── Perfil demo/custo — só os swaps com gate verde ──
# Tier 2: contextualização no Gemini Flash-Lite (empata gpt-4o-mini; editais são públicos).
# Afeta apenas ingests NOVOS (a coluna edital_chunks já está embedada com o contexto antigo).
CONTEXTUAL_RETRIEVAL_MODEL=gemini-2.5-flash-lite
CONTEXTUAL_RETRIEVAL_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
CONTEXTUAL_RETRIEVAL_API_KEY=${GEMINI_API_KEY}

# Tiers 1, 3, 4, 5: mantidos no modelo confiável (gate não autoriza troca ainda).
# NÃO setar EMBEDDING_MODEL, LLM_BACKEND, GEMINI_MODEL, ANTHROPIC_MODEL_AGENT aqui.
```

> ⚠️ Free-tier do Gemini tem RPM/RPD baixos — serve a demo, **não** re-ingest em lote.
> Para re-indexar o corpus inteiro com o novo contextualizador, use chave paga.

## O que destrava mais economia (pendências)

1. **Chave Gemini paga (centavos):** destrava os gates de **matching (t3)** e
   **extração (t4)** — o muro hoje é quota free, não qualidade.
2. **Golden de matching > N=2** + `build_knowledge_graph` (índice stale): dá força ao
   gate do tier 3.
3. **Gate de grounding confiável** (BACKLOG): pré-requisito pra sequer testar barato no
   tier 5 (writing). Só depois disso o agêntico entra no perfil de custo.

## Tier 5 — capability pronta, promoção bloqueada

O slot agêntico (writing + critic) já era trocável por modelo
(`ANTHROPIC_MODEL_AGENT`, `OPENAI_MODEL_AGENT`, `OPENAI_MODEL_CRITIC`). Faltava
poder mirar um endpoint **OpenAI-compat arbitrário** (DeepSeek/ZDR/local) — agora
suportado, com defaults inalterados:

| Env | Default | Efeito |
|---|---|---|
| `AGENT_OPENAI_BASE_URL` | canônico OpenAI | endpoint do provider "openai" do agente |
| `AGENT_OPENAI_API_KEY` | cai p/ `OPENAI_API_KEY` | key do endpoint (opcional em endpoint custom) |
| `CRITIC_OPENAI_BASE_URL` | cai p/ `AGENT_OPENAI_BASE_URL` → canônico | endpoint só do critic |
| `CRITIC_OPENAI_API_KEY` | cai p/ `AGENT_OPENAI_API_KEY` → `OPENAI_API_KEY` | key do critic |

**A capability existe; a promoção NÃO.** O tier 5 fica fora do bloco `.env` da demo
até (a) o gate de grounding ser confiável e (b) escolher um provider **ZDR/pago**
(nunca free-tier-com-treino — é dado de cliente). Exemplo de como apontar p/ DeepSeek
quando isso for liberado:

```bash
# SÓ quando o gate de grounding amadurecer + provider ZDR escolhido. Não usar na demo ainda.
AGENT_OPENAI_BASE_URL=https://api.deepseek.com/v1
AGENT_OPENAI_API_KEY=sk-deepseek-...
OPENAI_MODEL_AGENT=deepseek-chat
```
