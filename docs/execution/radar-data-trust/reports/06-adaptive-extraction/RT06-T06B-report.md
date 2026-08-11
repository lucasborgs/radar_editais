# RT06-T06B — Extração textual unificada de fatos decisórios

**Status:** `ready_for_validation`
**Plano:** [RT06-T06B](../../plans/06-adaptive-extraction/RT06-T06B-textual-unified-extraction.md)

## Realizado

- Produtor textual dedicado versionado como `text-v3`, sem adaptar o extrator
  legado para produzir o contrato novo.
- Uma chamada estruturada por lote cobre a família inicial e os alvos de
  prazo, janela, fluxo contínuo, valores, limites, contrapartida e tabelas.
- Contratos Pydantic validam datas, janelas, moeda, faixas, percentuais,
  contrapartida e referências textuais de tabela.
- O texto silver é dividido deterministicamente por seção/limite de tamanho e
  consolidado por documento; documento, página, seção, bloco e hashes continuam
  no artifact.
- Omissão, seleção parcial, valor inválido ou evidência não resolvida não
  produzem `absent`/`stated` indevidos. Estrutura de tabela perdida vira
  `unknown` com sinal diagnóstico.

## Validação executada

| Verificação | Resultado |
|---|---|
| Produtor real com cliente LLM fakeado apenas na fronteira | passou |
| Resposta única para todos os targets e contratos tipados | passou |
| Documento longo, seleção parcial e tabela sem estrutura | passou |
| Ruff no escopo alterado | passou |
| `git diff --check` | passou |

## Limitações

- Não há OCR, layout ou visão nesta task; T04 concluiu `no_escalation` no
  corpus disponível e T05 permanece `not_applicable`.
- `channel` permanece pendente por não haver entrada real disponível.
- T07 não foi promovida: faltam Postgres local, artifacts persistidos em
  ambiente de validação e goldens humanos/versionados.
