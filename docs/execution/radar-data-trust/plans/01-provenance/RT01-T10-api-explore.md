# RT01-T10 — API e Explorar

**Objetivo:** expor provenance opcional com fallback, sem quebrar payloads nem
fazer o agente completar lacunas pela memória do modelo.

## Entrega

- envelope público de citações aditivo;
- tools factuais retornam estado e evidência;
- `inferred`, `conflicting`, `unknown` e legado tratados explicitamente;
- atores sem corpus usam fatos estruturados, não RAG artificial.

## Validação

- testes de contrato da API e tools;
- casos factuais mínimos do Explorar;
- consumidores antigos continuam aceitos.

## Pare

Pergunte antes de alterar linguagem de produto, política de exposição ou o que
o usuário pode considerar “verificado”.
