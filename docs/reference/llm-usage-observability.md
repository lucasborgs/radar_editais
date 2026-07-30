# Observabilidade de usage e prompt cache

O runtime agrega quatro métricas, sem mudar prompts ou a política de cache:

- `input_tokens`: tokens de entrada cobrados/reportados pela chamada;
- `output_tokens`: tokens de saída;
- `cache_read_tokens`: tokens de entrada servidos pelo cache;
- `cache_write_tokens`: tokens de entrada gravados no cache.

Uma chave ausente significa que o provider não informou a métrica. Uma chave
presente com `0` é um zero reportado e não deve ser tratado como ausência.
Chamadas múltiplas do mesmo turno são somadas uma vez por mensagem de IA; não se
somam os spans filhos novamente no span agregado.

## Diferenças por provider

OpenAI raw usa `usage.prompt_tokens_details.cached_tokens` para leitura. O
runtime também aceita `prompt_tokens_details.cache_write_tokens` quando um
endpoint OpenAI-compatible realmente o devolver, embora o modelo tipado do SDK
OpenAI instalado possa não declarar esse campo. Anthropic raw usa
`cache_read_input_tokens` e `cache_creation_input_tokens`. LangChain normalizado
usa `usage_metadata.input_token_details.cache_read` e `.cache_creation`.
Shapes desconhecidos são ignorados de forma segura.

## Consulta operacional

No Langfuse, filtre observações por `provider`, `mode` e `model` no metadata
estruturado `turn_metrics`. O mesmo registro contém `llm_calls`, as quatro
métricas disponíveis, `stop_reason` e `runtime` (`langgraph`,
`langgraph-streaming` ou `langgraph-batch`). Logs estruturados usam o evento
`llm_turn_metrics` com os mesmos campos. Assim é possível calcular read share,
volume de write, custo líquido aproximado, relação com TTFT e a diferença entre
a primeira chamada e as chamadas 2+ do loop ReAct.

## Janela inicial e smoke manual

Observe pelo menos sete dias antes de tirar conclusões. Como verificação pontual
e opt-in da conexão com o provider, use o subcomando canônico abaixo; ele fica
fora das suítes normais e faz quatro chamadas sintéticas mínimas (duas sync e
duas streaming), cada par com prefixo estável de pelo menos 1.280 tokens e
variação somente no fim:

```bash
python -m radar.core.eval smoke-cache --allow-remote
```

Ele não lê dados de usuário, não publica no Langfuse e não altera prompt,
modelo, concorrência ou política da aplicação. A saída contém somente provider,
modelo, métricas, TTFT (streaming), runtime e stop reason. Em sync, TTFT fica
`None`, pois a API só entrega a resposta completa. Custo fica como não exposto
quando o payload do provider não o informar. Compare a chamada 1 com a 2 de
cada modo dentro da janela de elegibilidade do provider. `cache_read_tokens > 0`
na segunda chamada confirma hit; uma métrica ausente é inconclusiva, não zero.

Os testes unitários comprovam parsing, agregação e wiring; isoladamente, não
comprovam um hit real de cache. O smoke de 2026-07-29 com OpenAI
`gpt-4o-mini` confirmou `cache_read_tokens=1280` na segunda chamada dos modos
sync e streaming (`0` na primeira). Essa amostra confirma a reutilização do
prefixo, mas não um ganho de performance: sync melhorou no par observado,
enquanto streaming teve TTFT e runtime maiores na segunda chamada. Mantenha a
janela operacional de sete dias antes de otimizar prompts ou concorrência.
