# RT02-T05 — Mapa por camada, recomendação de maturidade e reconciliação

**Objetivo:** preencher o mapa da spec §6 com os resultados diagnósticos,
entregar a **recomendação** de maturidade (§7.4) e reconciliar a documentação
autoritativa (§13, §14). Fechamento da spec.

## Entrega

1. **Relatório** em `docs/execution/radar-data-trust/reports/02-quality-gates/`
   (criar a pasta aqui, no fechamento — não antes), com:
   - o mapa camada → suíte → classificação da §6 **preenchido** pelo baseline
     observado de cada suíte (incl. `provenance` e `e2e_health`);
   - por suíte existente: tamanho do golden, representatividade e execução (de T03);
   - **recomendação** de quais suítes têm baseline suficiente para o proprietário
     considerar promoção futura a gate — explicitando o baseline observado.
     **Recomendação, não decisão:** nenhum número é decretado como corte.
2. **Reconciliação documental** (§14): atualizar o status da spec 02, a tabela
   §9 da spec-mãe (`radar-data-trust.md`) e o mapa de camadas — sem copiar estado
   implementado para múltiplos docs. `AGENTS.md` e
   `docs/specs/evaluation-operations.md` ganham as duas suítes novas na lista,
   sem redefinir o contrato de `run`/`gate`.
3. **Reconciliação de redação (flag da governança):** a spec §7.1 enumera 6 casos
   de golden; a §13 critério 2 referencia "os casos obrigatórios da §10.2" (lista
   maior). O plano executou sobre §7.1 (seção Escopo, autoritativa), que exclui
   `conflicting`/`retificação` porque a spec 01 os encaminhou à spec 04. Registrar
   isso no relatório e sinalizar ao Fable se a redação de §13 deve ser alinhada a §7.1.

## Arquivos prováveis

- `docs/execution/radar-data-trust/reports/02-quality-gates/README.md` (+ um
  relatório por task executada, escritos pelos implementadores/governança);
- `docs/specs/radar-data-trust-02-quality-gates.md` (status/§6);
- `docs/specs/radar-data-trust.md` (§9, mapa de camadas);
- `AGENTS.md`, `docs/specs/evaluation-operations.md` (lista de suítes).

## Dependências

T02, T03, T04 (o mapa e o baseline dependem dos três resultados diagnósticos).

## Gate proporcional (fechamento — spec-mãe §11.5)

- `ruff` sobre o Python versionado;
- `pytest` da suíte completa contra o baseline da branch-base (regressão zero);
- rodar `provenance` e `e2e_health` local produzindo agregado estável;
- confirmar que nenhuma suíte nova é `gate` e nenhum threshold foi inventado;
- frontend/Docker/worker **não** aplicáveis (nenhuma task tocou wiring).

## Pare

Não marque a spec vigente com regressão não explicada, suíte instável entre
execuções, threshold inventado, ou qualquer suíte nova promovida a `gate`. A
"recomendação de maturidade" nunca vira decisão de promoção nesta task —
promoção é passo posterior, do proprietário, com corpus e baseline aceitos.
