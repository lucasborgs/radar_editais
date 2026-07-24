# RT02-T03 — Revisão leve das suítes existentes

**Objetivo:** confirmar que cada suíte diagnóstica existente roda, produz métrica
estável e está classificada corretamente; corrigir classificação ausente;
registrar tamanho do golden e se é representativo (§7.3). **Nenhum threshold é
adicionado.**

## Escopo

As suítes sem classificação/critério explícito (dependem hoje do default
`diagnostic` do dataclass `Suite`): `triage`, `structurer`, `opportunity_type`,
`rag`, `explore`, `reranker`, `profile_extractor`. (`extraction`=`gate`,
`matching`=`candidate`, `relevance_shadow`/`writing`/`writing_v2` já explícitas —
apenas confirmar, não mexer.)

## Entrega

Para cada suíte no escopo:

1. **classificação explícita:** tornar `classification="diagnostic"` explícito no
   `Suite(...)` do módulo (hoje é implícito pelo default) — correção da
   "classificação ausente" da §7.3, sem mudar o valor efetivo;
2. **execução:** confirmar que roda e produz métrica. Proporcional (spec-mãe
   §11.5): as determinísticas/baratas rodam local; as que exigem LLM/DB/creds
   (`rag`, `explore`, `profile_extractor`) rodam onde os prereqs locais forem
   satisfeitos barato (Postgres `:54322`, corpus seedado), senão registrar o
   prereq honestamente e deixar a rodada paga para o fechamento (T05/§13.6) — a
   revisão não muda prompt/modelo, então não obriga eval externa paga;
3. **golden:** registrar tamanho do golden e nota de representatividade (a
   contagem mede o corpus, não a representatividade — spec-mãe §4.1).

Estratificação de golden **só** onde a spec-mãe §4.1 apontou fragilidade
material E a medição da própria suíte justificar — não por princípio. Se nada
justificar, não estratifique.

## Arquivos prováveis

- `src/radar/core/eval/{triage,structurer,opportunity_type,rag,explore,reranker,profile_extractor}.py`
  (uma linha `classification=` cada, quando ausente);
- nota de revisão que alimenta o relatório de T05 (não criar `reports/02-*` aqui —
  T05/governança criam).

## Dependências

Nenhuma. Onda A (paralela a T01/T02/T04). Disjunta de `registry.py`.

## Gate proporcional

- `ruff` no escopo alterado;
- para cada suíte tocada, confirmar que `registry.py` a carrega e que `run`
  produz agregado;
- sem threshold, sem promoção de classificação.

## Pare

Não promova nenhuma suíte a `candidate`/`gate`, não adicione `criteria`, não
reescreva golden nem produtor. Se uma suíte exigir estratificação que a medição
não justifica, PARE e registre — não amplie o corpus por princípio. Suíte que
não roda por prereq ausente é um fato a registrar, não a mascarar.
