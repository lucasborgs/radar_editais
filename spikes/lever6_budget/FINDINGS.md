# FINDINGS — Item 6, Task 3: smoke de taxa de truncamento (budget_notice)

**Status:** dados coletados · **Data:** 2026-07-18 · **Decisão de promover/arquivar: NÃO tomada aqui — é da governança.**

Throwaway: `spikes/lever6_budget/demo.py` (mantém-se só se a governança pedir; os
números abaixo são o produto real desta task). Rodado contra os produtores reais
(`ExploreAgent`/`WritingSession`), provider resolvido para `openai`/`gpt-4o-mini`
(único disponível no ambiente).

## Amostra

Conjunto fixo, NÃO um eval (sem gate, sem golden) — 3 turnos de explore
multi-hop (força `list_editais`/`get_edital`/`list_icts`/`list_investidores`
repetidos) + 2 turnos de writing pedindo seção detalhada com instrução para
`search_edital`/`read_exact_chunk` (mesmo padrão do achado do spike do #2).
Cada bateria (baseline/treatment) roda os 5 turnos uma vez — **N pequeno,
resultado direcional, não estatístico**.

## Tabela ANTES/DEPOIS

| modo    | condição  | #turnos | #truncados | taxa  | avg llm_calls/turno |
|---------|-----------|---------|------------|-------|----------------------|
| explore | baseline  | 3       | 0          | 0.00  | 4.00                 |
| explore | treatment | 3       | 0          | 0.00  | 4.00                 |
| writing | baseline  | 2       | 1          | 0.50  | 10.50                |
| writing | treatment | 2       | 2          | 1.00  | 11.00                |

(baseline = `budget_notice` desligado via monkeypatch de `_build_graph` para a
topologia de 2 vias pré-Task 2; treatment = código real da Task 2, ligado.)

Eventos crus (`turn_end` parseado):

```
baseline: explore×3 (end_turn, llm_calls=4, max_steps=15)
          writing:  max_steps llm_calls=11 max_steps=10   (tratorbr/Equipe técnica)
          writing:  end_turn  llm_calls=10 max_steps=10   (biotecstartup/Cronograma)

treatment: explore×3 (end_turn, llm_calls=4, max_steps=15)
           writing:  max_steps llm_calls=11 max_steps=10  (tratorbr/Equipe técnica)
           writing:  max_steps llm_calls=11 max_steps=10  (biotecstartup/Cronograma)
```

## Leitura dos números (sem conclusão de promoção)

- **Explore:** 0% de truncamento nas duas condições, `avg_llm_calls=4` bem
  abaixo do teto (`max_steps=15`) — o aviso nunca dispara nesta amostra
  (dispararia só em `llm_calls==14`), então baseline e treatment são
  **idênticos** para este modo. Não há sinal de nenhum tipo aqui.
- **Writing:** o caso que estourou o teto no baseline (`tratorbr`, "Equipe
  técnica") **continua estourando** no treatment — mesmo `llm_calls=11`,
  mesma taxa. Mas o caso que **não** estourava no baseline (`biotecstartup`,
  "Cronograma", `llm_calls=10`, `stop_reason=end_turn`) **passou a estourar**
  no treatment (`llm_calls=11`, `stop_reason=max_steps`): a instrução do aviso
  ("faça no máximo UMA última chamada e então responda") parece ter levado o
  modelo a gastar exatamente mais UMA chamada de tool que não gastaria por
  conta própria — empurrando um turno que terminaria naturalmente dentro do
  teto para fora dele. Isto é o oposto do "sinal de sucesso" da spec (queda ou
  não-aumento da taxa **sem** inflar `avg_llm_calls`): aqui a taxa **subiu**
  (0.50→1.00) e a média de `llm_calls` também (10.50→11.00).
- **Amostra pequena** (N=2 em writing, N=3 em explore): um caso migrando de
  categoria já move a taxa em 50 pontos percentuais. Não dá para separar
  "o aviso piora sistematicamente" de "ruído de 1 caso" com este N — só um
  eval maior (fora do escopo desta task, que é smoke) resolveria isso.

## Nota de gate

Conforme a spec e o plano: nenhum eval rodou aqui (o aviso não muda a
evidência normativa vista pelo modelo). Este smoke não substitui o eval — só
mede a métrica de budget diretamente pedida pela Task 3.

## Próximo passo (não decidido aqui)

Os números acima vão para a governança. Cenários possíveis que ELES podem
escolher (não este script): arquivar o nó por não mostrar melhora na amostra
(e ter um caso pior), rodar uma amostra maior antes de decidir, ou investigar
por que o aviso empurrou o caso `biotecstartup` para +1 chamada.
