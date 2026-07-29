# RT05-T05 — API administrativa de exceções

## Objetivo

Expor a fila única ao operador e permitir decisão autenticada. É operação
interna, não sistema público de tickets; a UI fica exclusivamente em T06.

## Dependências

RT05-T02 a T04.

## Arquivos prováveis

- `src/radar/api/routers/data_quality.py` (novo) e `src/radar/api/app.py`;
- `tests/unit/test_data_quality_api.py` e testes de serviço afetados.

## Passos

1. Criar `GET /data-quality/exceptions`, detalhe e `POST .../reviews`, todos
   com `AdminUserId`, Pydantic estrito e `actor_id` vindo da autenticação.
2. Serializar somente sujeito, fonte, campo, código, valor seguro, evidência,
   versão, impacto e estado. Erro é categórico; sem nota interna, URL sensível
   ou payload bruto.
3. Preservar promoção/rejeição: revisão temporal não promove Web
   automaticamente.

## Invariantes

- Esta é a única escrita administrativa de revisão; sem edição, exclusão,
  comentários, SLA, retry, coleta ou bulk action.
- Usuário final nunca recebe a fila nem define `actor_id`.

## Testes mínimos

- admin/não-admin, lista vazia/filtros, detalhe sanitizado, decisão válida e
  decisão sem evidência rejeitada;
- `ENVIRONMENT=test pytest -q tests/unit/test_data_quality_api.py
  tests/unit/test_admin_gate.py`, `ruff check` e `git diff --check`.

## Critérios de aceite

- API permite recuperar Finep/Eureka e registrar decisão válida;
- endpoint é fail-closed e não expõe dados internos.

## Proibições

Sem frontend, workflow novo, notificações, comentários, API pública, mudança
em promover/rejeitar, LLM, rede, worker ou migration adicional.

## Pare se

A autorização não for fail-closed, dado bruto puder vazar ou a rota precisar
mudar estado editorial para funcionar.
