# Execução de specs

Esta área contém planos executáveis e relatórios de implementação ativos. Ela
não redefine produto, domínio ou arquitetura.

```text
docs/specs/       contrato normativo — o que deve existir
docs/execution/   execução ativa — como construir e o que aconteceu
docs/historical/  trabalho encerrado sem autoridade sobre o runtime atual
```

Cada iniciativa usa:

```text
<iniciativa>/
  README.md
  plans/<spec-filha>/<task>.md
  reports/<spec-filha>/<task>.md
```

## Regras

- um plano por task, curto e autocontido;
- um relatório versionado por task executada;
- o relatório consolidado da spec vive em `reports/<spec-filha>/README.md`;
- logs brutos, dumps, respostas LLM e outputs de ferramentas não são
  versionados; ficam no CI, em `eval_results/` ou em diretório temporário;
- golden aprovado, migration, regra de domínio e código vão para seus
  diretórios autoritativos, nunca permanecem como anexo de relatório;
- dúvida de produto volta ao proprietário antes da implementação; e
- ao encerrar a iniciativa, o diretório pode ser movido integralmente para
  `docs/historical/execution/`, com links reconciliados.

## Iniciativas ativas

- [`radar-data-trust/`](radar-data-trust/) — cobertura, proveniência e confiança
  do plano de dados.
