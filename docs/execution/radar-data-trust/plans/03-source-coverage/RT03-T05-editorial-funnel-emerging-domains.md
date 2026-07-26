# RT03-T05 — Funil editorial, lacunas e domínios emergentes

## Objetivo

Construir o read model determinístico que conecta `source_runs` às decisões
editoriais do staging. Ele mede rendimento por canal/família, pendências e
domínios emergentes; não muda nenhuma decisão e não cria fonte automática.

## Arquivos prováveis

- `src/radar/core/services/source_coverage_metrics.py` (novo);
- `tests/unit/test_source_coverage_metrics.py` (novo);
- fixtures mínimas em `tests/fixtures/source_coverage/`, se necessárias.

## Passos

1. Agregar runs por canal/família: última tentativa/sucesso, observados,
   emitidos, staged e rendimento `staged / candidates` apenas com denominador.
   Retornar `null` quando não for observável.
2. Consultar `discovered_opportunities` apenas em leitura para aprovados,
   rejeitados, pendentes, tempo descoberta→revisão e taxa editorial por canal e
   família. Linhas legadas com atribuição `null` continuam fora do denominador
   específico, mas podem aparecer como não atribuídas.
3. Definir lacunas como sinais explícitos, não score: canal habilitado sem run,
   run ambígua/atrasada, família sem dados suficientes ou fila pendente. Não
   concluir que há ausência de oportunidades ou cobertura ruim.
4. Agrupar somente `origin_domain` normalizado de oportunidades aprovadas;
   domínio com aprovações recorrentes é `candidate_for_dedicated_monitoring`.
   Expor contagem/período e nunca cadastrar fonte, scraper ou regra automática.
5. Manter a derivação de saúde separada e pura para T06: precedência da spec,
   duas janelas para `stale`, zero ambíguo `unknown` e sem segunda persistência.

## Invariantes

- Nenhuma métrica sem denominador retorna zero fabricado; lag e lacuna não são
  diagnóstico de relevância nem prova de recall.
- Domínio é hostname sem URL/path/query e só é candidato visual, nunca ação.
- Não carregar query completa, conteúdo, raw, erro bruto ou campos sensíveis.

## Testes direcionados

- funil por canal/família, linhas legadas, denominador ausente, pendência e
  tempo de revisão;
- todos os estados e precedência, zero ambíguo, duas janelas stale e flag
  desligada;
- domínio recorrente aprovado versus rejeitado/único, sem side effect;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_metrics.py`,
  `ruff check` no escopo e `git diff --check`.

## Pare

Pare se agregação exigir escrever staging, criar regra de promoção/fonte, usar
um threshold de recall, atribuir legado por inferência ou transformar lacuna em
prova de ausência. Não consultar DB remoto ou rede.

## Entrega e ambiente hermético

Entregar read model, fixtures/testes e relatório `RT03-T05-*.md` com
denominadores, lacunas e candidatos de fixture claramente rotulados. Confirmar
`ENVIRONMENT=test`, fake/DB local, sem `.env`, produção, Tavily, DOU, LLM ou
rede.
