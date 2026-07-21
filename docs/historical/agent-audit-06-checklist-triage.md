# 06 — Triage dos passes do Checklist (candidato #1)

**Fase:** 3 (model-routed) · **Validação:** testes (novos) + shadow · **Esforço:** médio

## Problema

`auto_review_checklist` roda **sempre os 3 passes LLM** (compliance + qualidade +
completude), independentemente do estado da proposta. Passes que não têm o que
avaliar (sem requisitos de edital, todas as seções já preenchidas) gastam uma
chamada LLM para confirmar o óbvio.

## Estado atual

- `auto_review_checklist` (`core/services/checklist_service.py:320`): orquestra a
  revisão automática.
- Os 3 passes disparam **incondicionalmente** via `asyncio.gather`
  (`checklist_service.py:363`) — compliance, qualidade, completude em paralelo.
- **Modelo único** nos 3 passes (gpt-4o-mini default); sem roteamento por passe.
- **Input idêntico:** `proposal[:6000]` alimenta os 3 passes.
- **Sem testes:** não existe `tests/unit/test_checklist_service.py`.

## Mudança proposta

**Gates determinísticos antes do `gather` (`:363`):** decidir *quais* passes rodar
sem LLM, com base em sinais já disponíveis:

1. **Pular compliance** se `edital_requirements` está vazio — não há regra contra a
   qual checar; o passe só produziria "nada a verificar".
2. **Pular completude** se todas as seções estão preenchidas — completude é
   estrutural e checável sem LLM (presença/tamanho mínimo por seção).
3. **Sempre rodar qualidade** (default) — é subjetivo, não tem gate barato confiável.

O hook de triage entra na linha 363: monta a lista de passes elegíveis e só passa
esses ao `gather`. Passes pulados retornam um resultado sintético "ok / não
aplicável" para não quebrar o consumidor.

## Por que ganha custo

Cada passe pulado = uma chamada LLM a menos por revisão. Em propostas com edital
sem requisitos estruturados ou já completas, corta 1–2 dos 3 passes. O gate é
puramente determinístico (custo ~zero) vs o custo do passe LLM evitado.

## Validação

- **Testes (pré-requisito):** criar `tests/unit/test_checklist_service.py` cobrindo o
  comportamento atual **antes** de mexer — sem rede de segurança hoje. Casos:
  edital sem requisitos → compliance pulado; seções completas → completude pulada;
  proposta parcial → 3 passes.
- **Shadow:** rodar all-3 vs triaged em paralelo num conjunto de propostas reais e
  **comparar o set de issues**. Promover só se o triaged **não perde nenhum
  achado** que o all-3 produzia (o risco é um gate pular um passe que teria pego
  algo).

## Risco

Médio: um gate cedo demais (ex.: "seções preenchidas" mas com conteúdo ruim que
completude pegaria) esconde um achado. Mitigação = gates conservadores (só pular
quando o passe é comprovadamente vazio) + shadow comparando issues antes de
promover.

## Perguntas em aberto

- O gate de completude deve checar só presença de seção, ou também tamanho mínimo /
  placeholders? Começar com presença; apertar se o shadow mostrar falsos "completo".
- Vale rotear o passe de qualidade para um modelo melhor com o custo economizado
  nos passes pulados? Fora do escopo deste item; anotar se o shadow sugerir.
