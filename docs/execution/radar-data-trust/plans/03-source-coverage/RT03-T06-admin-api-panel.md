# RT03-T06 — API administrativa e painel

## Objetivo

Expor o read model de T05 via `GET /source-coverage`, protegido por
`AdminUserId`, e mostrar painel recolhido no topo de `/discovered`. É leitura
operacional de canais/famílias, não controle de fontes.

## Arquivos prováveis

- `src/radar/api/routers/source_coverage.py` (novo) e `src/radar/api/app.py`;
- `tests/unit/test_source_coverage_api.py` (novo);
- `frontend/src/lib/api.ts` e `frontend/src/app/discovered/page.tsx`;
- teste frontend próximo ao padrão existente, se aplicável.

## Passos

1. Serializar payload sanitizado de T05: `generated_at`, canais/estados, última
   tentativa/sucesso, contadores/rendimento, famílias, lacunas, domínios
   emergentes e limitações. Tabela vazia retorna todos os canais como
   `unknown`/`disabled`, sem fabricar métricas.
2. Criar router mínimo que usa `AdminUserId` e service role, registrar no app e
   retornar erro categórico sem DSN/erro bruto. Não criar endpoint de escrita,
   edição, query, flag, retry ou crawler.
3. Adicionar cliente tipado e painel recolhido, com texto canônico **“Fontes e
   canais monitorados pelo Radar”**. Mostrar saúde, rendimento, famílias,
   domínios candidatos e limites; não afirmar cobertura do Brasil.
4. Tratar API indisponível/403 como fallback discreto que não bloqueia lista,
   filtro, promoção ou rejeição de Descoberta.

## Invariantes

- API/painel são admin-only e read-only; saúde continua derivada, sem cache ou
  coluna nova.
- Frontend recebe somente dados sanitizados; domínio não vira ação automática.
- Nenhum fluxo editorial, registry, flag ou source run é mutado pelo usuário.

## Testes direcionados

- `AdminUserId`, tabela vazia, sanitização, erro categórico e payload com
  `null` para denominador ausente;
- painel/fallback sem bloquear a fila existente;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_api.py
  tests/unit/test_admin_gate.py`, `ruff check` no escopo;
- `cd frontend && npx tsc --noEmit`, `cd frontend && npm run lint` e
  `git diff --check`.

## Pare

Pare se API não for fail-closed/admin-only, UI ganhar operação, erro/URL/query
sensível escapar, lacuna virar score/recall ou a validação usar rede/produção.

## Entrega e ambiente hermético

Entregar router/wiring/cliente/painel/testes e relatório `RT03-T06-*.md` com
payload vazio e fallback. Confirmar `ENVIRONMENT=test`, DB fake/local e frontend
local, sem `.env` produtivo, Cloud, rede, Tavily, DOU ou LLM.
