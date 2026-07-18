# FINDINGS — Item 6, Task 3: smoke de taxa de truncamento (budget_notice)

**Status:** VEREDITO APLICADO (regra pré-registrada, iteração única) · **Data:** 2026-07-18

Throwaway: `spikes/lever6_budget/demo.py`. Rodado contra os produtores reais
(`ExploreAgent`/`WritingSession`), provider resolvido para `openai`/`gpt-4o-mini`
(único disponível no ambiente). Duas iterações: a 1ª motivou um reword do
prompt (achado: menção a "uma última chamada" induzia o modelo a exercê-la);
a 2ª (esta) reroda com o texto novo e amostra maior de writing, e aplica a
regra de promoção pré-registrada pela governança. **Não há 3ª iteração.**

---

## Iteração 1 (histórico — motivou o reword)

Amostra original: 3 explore + 2 writing (`tratorbr`, `biotecstartup`), texto
antigo de `_LAST_STEP_PROMPT` (mencionava "faça no máximo UMA última
chamada").

| modo    | condição  | #turnos | #truncados | taxa  | avg llm_calls/turno |
|---------|-----------|---------|------------|-------|----------------------|
| explore | baseline/treatment | 3 | 0    | 0.00  | 4.00                 |
| writing | baseline  | 2       | 1          | 0.50  | 10.50                |
| writing | treatment | 2       | 2          | 1.00  | 11.00                |

Achado: o caso `biotecstartup` (não-truncado no baseline, 10 chamadas) passou
a truncar no treatment (11 chamadas) — a menção a "uma última chamada"
parecia induzir o modelo a exercê-la. Isso motivou o reword de
`_LAST_STEP_PROMPT` (commit `6cb536d1d`): proibição direta ("NÃO chame mais
tools"), sem abrir a porta para "mais uma".

---

## Iteração 2 (final) — prompt reescrito + amostra maior

Amostra: explore mantido em N=3 (mesmas perguntas). Writing expandido para
**N=4** por condição: os 2 originais (`tratorbr`, `biotecstartup`, mantidos
para comparabilidade) + 2 novos (`espectra`/Contrapartida financeira,
`agrosoftsys`/Critérios de elegibilidade), mesmo padrão
search_edital+read_exact_chunk.

### Tabela ANTES/DEPOIS

| modo    | condição  | #turnos | #truncados | taxa  | avg llm_calls/turno |
|---------|-----------|---------|------------|-------|----------------------|
| explore | baseline  | 3       | 0          | 0.00  | 4.00                 |
| explore | treatment | 3       | 0          | 0.00  | 4.67                 |
| writing | baseline  | 4       | 4          | 1.00  | 11.00                |
| writing | treatment | 4       | 2          | 0.50  | 10.50                |

### Detalhe por variante de writing (ORIGINAL = também rodou na iteração 1)

| variante      | origem   | baseline stop_reason (llm_calls) | treatment stop_reason (llm_calls) |
|---------------|----------|-----------------------------------|-------------------------------------|
| tratorbr      | ORIGINAL | max_steps (11)                    | max_steps (11)                      |
| biotecstartup | ORIGINAL | max_steps (11)                    | end_turn (10)                       |
| espectra      | novo     | max_steps (11)                    | end_turn (10)                       |
| agrosoftsys   | novo     | max_steps (11)                    | max_steps (11)                      |

**Nota de reprodutibilidade:** `biotecstartup` truncou no baseline desta
iteração (11 chamadas) mas NÃO truncava no baseline da iteração 1 (10
chamadas) para a mesma instrução/perfil — cada rodada usa uma `WritingSession`
nova (sessão/thread independente) e o modelo não roda a `temperature=0`, então
o próprio baseline varia entre execuções independentes. Isso é esperado (LLM
real, não determinístico) mas limita a confiança do resultado a "direcional",
não estatístico — ver Amostra/N abaixo.

---

## Regra pré-registrada (anunciada antes de olhar o resultado desta iteração)

> **PROMOVE** se `taxa(treatment) <= taxa(baseline)` **E**
> `avg_llm_calls(treatment) <= avg_llm_calls(baseline) + 0.25`.
> Caso contrário, **ARQUIVA** (revert da Task 2). Sem segunda iteração.

Aplicação mecânica aos números do modo **writing** (o único onde o aviso
dispara nesta amostra — explore nunca chega perto do teto):

- `taxa(treatment)=0.50 <= taxa(baseline)=1.00` → **verdadeiro**
- `avg_llm_calls(treatment)=10.50 <= avg_llm_calls(baseline)=11.00 + 0.25 (=11.25)` → **verdadeiro**

**→ PROMOVE.** Ambas as condições da regra pré-registrada são satisfeitas.
`budget_notice` (Task 2, com o texto reescrito da iteração 2) permanece no
grafo — nenhum revert foi executado.

## Amostra e limitações (honestas, não escondidas pelo veredito)

- N=4 por condição em writing, N=3 em explore — ainda pequeno; a regra foi
  aplicada mecanicamente como pré-registrado, mas 1-2 casos migrando de
  categoria moveriam o resultado (ver nota de reprodutibilidade acima: o
  próprio baseline já variou entre iterações para o mesmo caso).
- Explore permanece sem sinal (0% truncamento nas duas condições nas duas
  iterações) — o aviso nunca dispara aí (bem abaixo do teto de 15 passos).
- Nenhum eval rodou (nota de gate da spec): o aviso não muda a evidência
  normativa vista pelo modelo, só sinaliza budget.

## Encerramento

Regra pré-registrada aplicada, veredito = PROMOVE, sem revert. Esta é a
iteração final — nenhuma nova rodada de smoke está planejada para este item.
