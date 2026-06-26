# Spec: Lazy chunking (estilo NotebookLM)

Status: proposta · 2026-06-26 · reverte comportamento eager do cron

## Decisão

O **chunking** (corpus `edital_chunks`, RAG fino) passa de **EAGER** (o cron
`run_daily_etl` chunka todo edital que o scraper acha) para **LAZY** (chunka só os
editais que o usuário **engaja para escrever**). Navegação e match continuam no
**tier grosso** (KG + wiki pages), que cobre o catálogo inteiro e não usa chunks.

## Motivação (two-tier retrieval / NotebookLM)

- **Tier grosso** — KG + wiki (LLM-wiki): navegar o grafo, match, brief. Barato,
  cobre tudo.
- **Tier fino** — chunk-RAG: escrever sobre as **fontes selecionadas**, igual o
  NotebookLM só indexa o que você adiciona.

O custo de embedding passa a escalar com **uso**, não com tamanho do catálogo —
coerente com a ideação do produto (gera/navega via wiki; escreve via RAG nos docs
selecionados).

## Mapa de consumidores de chunk (verificado no código)

| Consumer | Fluxo | Escopo |
|---|---|---|
| `writing_session.py` (RAG por turno) | Escrita | edital selecionado |
| `writing_tools.search_edital` | Escrita | idem |
| `critic_agent` (grounding) | Escrita | idem |
| `explore_tools.search_edital_trechos` | Explore | qualquer edital (degrada p/ wiki) |

**NÃO usam chunks:** match (`retriever.py:12` — summary embeddings) e
OpportunityBrief (`opportunity_brief_service.py:92` — wiki, ADR M9).

## Mudanças

1. **Prefetch (async):** `POST /opportunity/brief` defere `chunk_edital(edital_id)`.
   É o sinal de intenção que precede a escrita → aquece os chunks enquanto o usuário
   lê o brief. Mascara o cold-start.
2. **Ensure (rede de segurança):** `POST /writing/start` garante chunks antes da
   geração do primeiro turno (que faz RAG). Se ausentes, materializa antes de gerar.
   Raro, pois o brief já aqueceu. Idempotente via gate de `content_hash`.
3. **Cron:** remover o `defer chunk_edital` de `run_daily_etl` (`tasks.py:595`).
   O cron segue rodando scrapers + `build_knowledge_graph` (navegação/match).
4. **Explore degrada explícito:** `search_edital_trechos` sem chunks → retorna a
   wiki page / "edital ainda não indexado para busca fina", nunca vazio silencioso.
5. **Wipe:** `TRUNCATE edital_chunks` (prod + local). O corpus renasce lazy conforme
   usuários engajam. (Perde os 31 web atuais — aceito; corpus stale era-large.)

## Não-objetivos (anti-over-engineering, fase de validação)

Pré-indexação especulativa do catálogo, chunk-on-hover, fila de prefetch elaborada.
Revisitar só com usuários reais e sinal de latência.

## Observabilidade

Materialization ratio = editais chunkados ÷ catálogo. Valida que a economia é real.

## Riscos / trade-offs

- **Cold-start** no 1º writing sem brief prévio (~40s). Mitigado pelo prefetch; o
  ensure-at-start é a rede. Cap de timeout do HTTP a considerar na implementação.
- **Explore-trechos parcial** em editais nunca escritos — cai na wiki (aceito).
