# RT03-T06 — Agregação, API administrativa e painel somente leitura

## Objetivo

Expor um read model conservador de `source_runs` e registry em
`GET /source-coverage`, protegido por `AdminUserId`, e um painel recolhido no
topo de `/discovered`. A interface informa limites e não opera fontes.

## Arquivos prováveis

- `src/radar/core/services/source_coverage.py`;
- `src/radar/api/routers/source_coverage.py` (novo) e `src/radar/api/app.py`;
- `tests/unit/test_source_coverage_read_model.py` e
  `tests/unit/test_source_coverage_api.py` (novos);
- `frontend/src/lib/api.ts`;
- `frontend/src/app/discovered/page.tsx`;
- teste frontend próximo ao padrão existente, se houver cobertura de componente.

## Passos

1. Construir agregador puro/leitura que une todos os canais do registry às
   linhas existentes, inclusive tabela vazia. Expor `generated_at`, totais
   registrados/habilitados/observados, distribuição de estados, última tentativa,
   último sucesso, últimos contadores seguros, métricas de Descoberta e snapshots
   de catálogos.
2. Derivar estado sem gravar: aplicar precedência `disabled → failing → degraded
   → stale → healthy → unknown`; `disabled` consulta somente a flag declarada.
   `healthy` requer conclusão comprovada na janela; run vazio ambíguo não a
   satisfaz. `stale` só para periódico com duas janelas; catálogo não recebe
   estado healthy/stale inventado.
3. Agregar `staged / candidates` somente com denominador explícito; para
   decisão editorial, consultar promovidos/rejeitados/pendentes por período sem
   reescrever staging. Se dado não existe, retornar `null`, não zero. Sanitizar
   razão/métricas antes de serializar.
4. Criar router pequeno com `AdminUserId` e service role, registrar no app e
   testar 403/fail-closed pelo mesmo padrão de `/discovered-opportunities`.
   Tabela vazia deve retornar todos os canais `unknown` ou `disabled` e status
   200; indisponibilidade do DB deve receber erro categórico, sem detalhe de DSN.
5. Adicionar cliente tipado e painel recolhido por padrão. Mostrar o texto
   canônico **“Fontes monitoradas pelo Radar”**, estado, última execução/sucesso,
   contadores essenciais e limitações. Falha/403/indisponibilidade não bloqueia
   carregar, filtrar, promover ou rejeitar a fila. Não incluir botões de edição,
   retry, reexecução ou flags.

## Invariantes

- API e painel são somente leitura, de operador; não são uma alegação de
  cobertura nacional nem console de observabilidade genérico.
- Estado e métricas são derivados sob demanda; `source_runs` não ganha coluna
  de saúde e não se cria cache/segunda fonte de verdade.
- O frontend não recebe campos sensíveis e conserva todos os fluxos da fila.

## Testes direcionados

- cada estado e precedência, dois intervalos de stale, zero ambíguo, catálogo
  sem SLA e métrica sem denominador;
- auth de admin, tabela vazia, dados sanitizados e falha categórica do serviço;
- fallback visual da API indisponível sem bloquear a lista de Descoberta;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_read_model.py
  tests/unit/test_source_coverage_api.py tests/unit/test_admin_gate.py`;
- `ruff check` no Python afetado, `cd frontend && npx tsc --noEmit`,
  `cd frontend && npm run lint` e `git diff --check`.

## Pare

Pare se a UI ganhar ação operacional, a API não for admin-only, um catálogo for
marcado saudável/stale sem política aprovada, uma métrica sem denominador virar
zero, ou a leitura exigir rede/produção. Não exibir exceção, URL, query, texto,
chave ou traceback.

## Entrega e ambiente hermético

Entregar agregador, router, wiring, cliente/painel e testes, com relatório
`RT03-T06-*.md` que inclui payload de tabela vazia e fallback da UI. Confirmar
`ENVIRONMENT=test`, DB fake/local e frontend local sem `.env` de produção,
Supabase Cloud, rede, Tavily, DOU ou LLM.
