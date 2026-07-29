# RT05-T08 — Comunicação de validade nos consumidores

## Objetivo

Comunicar o payload canônico de T07 em Radar, Ecossistema, Explorar e Escrita.
`needs_review` recebe **Validade a confirmar**, não aparece como aberto e nunca
ganha prazo inventado.

## Dependências

RT05-T06 e RT05-T07.

## Arquivos prováveis

- `frontend/src/lib/api.ts`, `frontend/src/lib/radar-utils.ts`,
  `frontend/src/app/radar/page.tsx` e páginas de oportunidade afetadas;
- renderizadores/serializadores existentes de Explorar e Escrita;
- testes frontend e de apresentação temporal existentes.

## Passos

1. Exibir no Ecossistema item incerto como histórico/consultável com
   **Validade a confirmar** e fonte/data fornecidas pelo payload.
2. Garantir que Radar não apresenta `needs_review` como edital aberto nem usa
   "Contínuo / sem prazo" para ausência não confirmada.
3. Fazer Explorar e Escrita renderizarem o estado recebido: não afirmar abertura,
   prazo ou continuidade incerta. Ajustar renderizadores/blocos existentes,
   sem criar prompt de decisão paralelo.
4. Tratar payload legado/ausente conservadoramente e manter tela útil; não buscar
   dados extras nem colocar regra temporal em JavaScript.

## Invariantes

- Frontend e prompts reutilizam `validity_state`/`temporal_mode` de T07 e não
  recalculam temporalidade.
- Não expor review, justificativa interna, ator ou payload bruto.
- Não mudar ranking, match, extração, relevância ou fluxo de escrita.

## Testes mínimos

- Finep/Eureka exibe incerteza no Ecossistema e não aparece aberto no Radar.
- Explorar/Escrita recebem aviso conservador; legado não afirma continuidade.
- `cd frontend && npx tsc --noEmit`, `cd frontend && npm run lint`, testes
  focados de apresentação e `git diff --check`.

## Critérios de aceite

- Usuários veem mensagem coerente em todas as superfícies.
- Não há regra temporal duplicada em frontend, prompt ou filtro.

## Proibições

Sem API/migration/backend novo, LLM, OCR/visão, fetch adicional, estado local
de revisão, ação pública, backfill ou redesign geral.

## Pare se

O payload canônico não bastar para comunicação segura, se algum texto exigir
inferência de prazo ou se a UX histórica exigir decisão fora da spec.
