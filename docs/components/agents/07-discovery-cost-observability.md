# 07 — Descoberta: cache negativo + observabilidade de custo (candidato #4)

**Fase:** 3 (model-routed) · **Validação:** dry-run · **Esforço:** baixo-médio

## Contexto (reformulação do finding)

A Descoberta **já é model-routed** corretamente: `_triage` usa gpt-4o-mini
(`opportunity_discovery.py:124`) e `_extract` usa gpt-4o
(`opportunity_discovery.py` no `_extract`). O roteamento por custo está feito.

O problema real é **outro**: o desperdício não está no modelo, está no *re-trabalho*.

## Problema

URLs **descartadas** na triagem não entram no ledger
(`opportunity_discovery.py:447`). Como o cron diário (`discover_opportunities`,
04:00 UTC — `core/tasks.py:643`) re-varre as mesmas fontes, as mesmas URLs
rejeitadas são **re-triadas (re-pagas)** toda rodada. Além disso, os descartes
**não são logados** — não há visibilidade de quanto se gasta confirmando lixo já
conhecido.

## Estado atual

- `_triage` (`opportunity_discovery.py:124`): gpt-4o-mini decide candidato vs
  descarte.
- `_extract`: gpt-4o extrai os candidatos aprovados.
- Ledger gravado em `:447` — **só os aprovados/processados** entram; descartes não
  deixam rastro.
- Cron `discover_opportunities` 04:00 UTC (`core/tasks.py:643`) — re-executa a
  varredura diariamente sobre fontes que mudam pouco.
- `write=False` já existe (`opportunity_discovery.py:350`) — há um dry-run nativo.

## Mudança proposta

1. **Cache negativo:** persistir URLs rejeitadas na triagem com **TTL** (ex.: 30
   dias). Antes de chamar `_triage`, consultar o cache — URL rejeitada e ainda
   dentro do TTL é pulada sem chamada LLM. TTL evita prender para sempre uma URL
   que pode virar relevante (conteúdo muda).
2. **Log de descarte:** registrar cada rejeição (URL, motivo curto da triagem,
   timestamp) — observabilidade do custo evitado e auditoria do que a triagem está
   jogando fora (pega falso-negativo da triagem).

## Por que ganha custo

A triagem é a chamada mais frequente (todo candidato passa por ela). Cache negativo
elimina a re-triagem diária das mesmas URLs lixo → corta o grosso das chamadas
gpt-4o-mini em rodadas subsequentes, sem tocar a qualidade (URL nova ou
TTL-expirada ainda é triada normalmente).

## Validação

- **Dry-run:** rodar com `write=False` (`:350`) e medir, numa janela de N dias
  simulados, quantas chamadas `_triage` o cache negativo elimina vs baseline.
- Verificar no log de descarte que nenhuma URL **antes aprovada** aparece sendo
  bloqueada pelo cache (o cache só guarda rejeições).

## Risco

Baixo: o cache só *pula* o que a triagem já rejeitaria. O único risco é prender uma
URL cujo conteúdo passou a ser relevante — mitigado pelo TTL. TTL curto = mais
seguro / menos economia; calibrar pelo log.

## Perguntas em aberto

- TTL do cache negativo: 30 dias é chute. Calibrar pela cadência real de mudança
  das fontes (fontes estáveis toleram TTL maior).
- Onde persistir o cache: tabela própria vs reusar o ledger com um status
  `rejected`? Reusar o ledger é mais simples e dá a observabilidade de graça —
  preferir, se o schema do ledger comportar.
