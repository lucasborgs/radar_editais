# Dados de avaliação

Este diretório separa a autoridade dos casos usados pelo harness:

- `golden/`: casos autoritativos ou datasets diagnósticos ativos, consumidos
  por suítes registradas em `core/eval/registry.py`;
- `historical/`: casos preservados como evidência, sem consumidor ativo e sem
  autoridade sobre as suítes atuais.

Resultados de execução não pertencem aqui. Eles são gravados em
`eval_results/` (ignorado pelo Git); runs publicadas têm histórico oficial no
Langfuse.
