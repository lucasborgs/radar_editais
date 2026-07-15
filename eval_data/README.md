# Dados de avaliação

Este diretório separa a autoridade dos casos usados pelo harness:

- `golden/`: casos autoritativos ou datasets diagnósticos ativos, consumidos
  por suítes registradas em `core/eval/registry.py`;
- `historical/`: casos preservados como evidência, sem consumidor ativo e sem
  autoridade sobre as suítes atuais.

Resultados de execução não pertencem aqui. Eles são gravados em
`eval_results/` (ignorado pelo Git); runs publicadas têm histórico oficial no
Langfuse.

## Histórico preservado

- `historical/compliance_monitor.json`: corpus do monitor de compliance
  anterior, sem suíte ou pipeline de avaliação registrado no runtime atual;
- `historical/investor_match.json`: fixture do matching de investidores
  anterior, sem consumidor vivo após a adoção do matching gold v3.

Esses arquivos foram movidos, não apagados, porque ainda documentam avaliações
anteriores e sua ausência de consumidor não prova ausência de valor histórico.
