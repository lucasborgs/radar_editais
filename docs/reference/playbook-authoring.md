# Guia de autoria de playbooks (skills de competência)

Receita reutilizável para escrever a skill de cada mecanismo. Gabarito de
referência: [skills/mechanism/subvencao.md](../../skills/mechanism/subvencao.md)
(+ overlay [skills/source/finep/subvencao.md](../../skills/source/finep/subvencao.md)).
O loader e a composição vigentes estão em `core/skills.py`; a decisão de design
original está preservada em
[`skills-by-mechanism.md`](../historical/skills-by-mechanism.md).

## Fluxo (por mecanismo)

1. **Entrevista** — rodar o template abaixo (trocando `<MECANISMO>`) com o fundador
   e/ou LLMs externas. As respostas ricas e específicas são a matéria-prima.
2. **Destilar** nas 3 seções roteadas, com disciplina (abaixo). **Cortar sem dó** —
   a skill é injetada todo turno; valor = afiada e curta, não exaustiva.
3. **Separar fato↔craft** — o que muda por edital sai da skill (vai pro RAG/grafo);
   o que é específico de fonte vai pro overlay `source/<fonte>/<mech>.md`.
4. Marcar como SEED pendente de validação por outcome (learning loop, BACKLOG).

## Disciplina de destilação

- **Craft é o payload; fato é rationale curto** (em itálico). NUNCA número, prazo,
  rubrica, elegibilidade, % — isso é do edital.
- **Delta sobre a persona-base** (o system prompt já é "especialista em propostas")
  — não repita craft genérico; só o que muda para este mecanismo.
- **Seções = tipos = roteamento** (cabeçalho `##` + comentário do consumidor):
  - `## Padrões de escrita e tom` → Redator (geração)
  - `## Heurísticas de aprovação` → ComplianceMonitor (avaliação)
  - `## Anti-padrões / red flags` → ComplianceMonitor (avaliação)
- **Rodapé fato↔craft** como guarda-corpo (regra de bolso: "muda ao trocar a
  fonte/ano? então é fato → RAG").
- Use **pares "vago → específico"** e **listas de instant-kills** — guiam o LLM
  melhor que princípios.

## Template de entrevista (trocar `<MECANISMO>`)

```markdown
# Entrevista — Playbook de escrita: <MECANISMO> (fomento BR)
Contexto: playbook = COMPETÊNCIA tácita de redação (não regra do edital, que vem
do RAG). Responda com especificidade, exemplos, pares "ruim→bom", instant-kills.

## A — Lente e arco narrativo (→ Padrões de escrita e tom)
1. Qual o arco narrativo que aprova (do quê pro quê)?
2. Qual a "lente" do avaliador deste instrumento — o que ele realmente compra?
3. Como provar viabilidade/capacidade sem soar do gênero errado?

## B — Craft por seção (→ Padrões de escrita e tom)
4. Por seção (justificativa, objetivos, metodologia, resultados, equipe,
   orçamento, cronograma): qual o "trabalho" dela e o erro que mata?
5. Como amarrar as seções numa cadeia coerente? Qual incoerência mais reprova?

## C — Rubrica oculta (→ Heurísticas de aprovação)
6. O que pesa na PRÁTICA vs no papel do edital?
7. "Sim com convicção" vs "sim com ressalvas" — o que separa?

## D — Red flags (→ Anti-padrões)
8. Instant-kills (reprova/rebaixa rápido)?
9. Erros que BOAS empresas cometem por desconhecer a praxe?

## E — Língua e sinais (→ Padrões + Anti-padrões)
10. Termos que sinalizam amadorismo/gênero errado?
11. Pares de reescrita "ruim → bom".

## F — Praxe por fonte/programa (→ overlay de fonte; pode conter FATO)
12. Como as fontes diferem na MESMA proposta deste mecanismo?
13. Programas distintos mudam o enquadramento?

## G — Fronteira fato↔craft (meta)
14. O que aí é FATO (edital/RAG) vs CRAFT (playbook)? Regra: sobrevive à troca de
    fonte/ano = craft; precisa atualizar por edital = fato.
```

## Fila de mecanismos

- [x] `subvencao` (gabarito) + overlay `finep`
- [x] `credito` (financiamento reembolsável) + overlay `finep` — lente: capacidade de pagamento/fluxo de caixa
- [~] `bolsa` — **FORA DE ESCOPO** (2026-06-14): o sistema não atende o público de
  bolsas. Playbook intencionalmente não autorado; edital classificado como `bolsa`
  cai no fallback genérico (D3). Reabrir só se o escopo mudar.
- [~] `matching` (EMBRAPII) — **ADIADO (BACKLOG, 2026-06-14)**: EMBRAPII/ICT são
  insumo do Match, não da escrita. Template-semente em `playbook-interview-matching.md`.
- [x] `equity` (pitch) — **outro gênero**, roteado ao agente de pitch (mode=pitch),
  não compliance. Overlay por tipo de investidor/estágio adiado (sem demanda).
- Overlays de fonte pendentes: `source/fapesp/*` (lado PIPE do contraste F12).
