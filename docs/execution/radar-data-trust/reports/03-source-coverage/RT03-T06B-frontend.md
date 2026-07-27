# RT03-T06B — Painel de cobertura (frontend)

## Resultado

Painel recolhível "Fontes e canais monitorados pelo Radar" no topo de
`/discovered`, consumindo `GET /source-coverage` (endpoint administrativo de
T06-A). Falha da API não bloqueia a fila editorial.

## Histórico

A tentativa anterior (commit `9f88e8896`) foi descartada porque inventou
endpoint `/stats/source-coverage` e payload com campos `source_id`,
`source_label`, `last_run_at`, `records_ingested`, `error_message`,
`grace_period_hours` e `updated_at` — todos inexistentes no backend real.

## Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `frontend/src/lib/api.ts` | Tipos `ChannelHealth`, `ChannelRunMetrics`, `EditorialFunnel`, `FamilyFunnel`, `CoverageGap`, `EmergingDomain`, `ChannelHealthStatus`, `SourceCoverageResponse` e função `getSourceCoverage` — espelho do contrato de `source_coverage.py` |
| `frontend/src/app/discovered/page.tsx` | Estado/efeito isolado, painel recolhível com 6 seções compactas |

## Comportamento

- **Recolhido**: "Fontes e canais monitorados pelo Radar — X saudáveis, Y com
  problema" (só categorias não-zero aparecem)
- **Expandido**: até 6 seções compactas:
  1. badges de canal + estado (saudável, degradado, falhando, atrasado,
     desativado, desconhecido)
  2. tabela de execuções (última tentativa, último sucesso, observados,
     emitidos, stage, rendimento)
  3. funil editorial por família (aprovados, rejeitados, pendentes, taxa,
     revisão média)
  4. lacunas (badges âmbar)
  5. domínios candidatos a monitoramento dedicado (badges azuis)
  6. limitações da API (lista)
- **`null`**: exibido como `—` ou "sem denominador", nunca zero fabricado
- **Cobertura vazia**: mostra geração sem canais (sem crash)
- **Falha/403**: painel mostra "Painel indisponível no momento." — não altera
  `forbidden`, não mostra toast, não bloqueia promoção/rejeição/filtros
- **Nenhum botão operacional** no painel

## Validação

- `npx tsc --noEmit`: 0 erros
- `npm run lint`: sem warnings novos (apenas 4 preexistentes)
- `ENVIRONMENT=test pytest -q test_source_coverage_api.py
  test_source_coverage_metrics.py test_admin_gate.py`: 81 passed
- `git diff --check`: sem whitespace errors
- Backend T06-A inalterado (0 arquivos .py no diff)

## Auditoria Codex: pendente
