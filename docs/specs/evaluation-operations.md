# Spec — Avaliação e operação

**Status:** aprovada; implementação operacional concluída, matching ainda
candidato · **Data:** 2026-07-14
**Documento-pai:** [`system-coherence.md`](system-coherence.md)
**Perfis afetados:** usuário técnico e operador
**Impacto:** médio; harness, metadados de avaliação e gates operacionais, sem
alteração dos pipelines avaliados

## 1. Problema comprovado

O repositório possui um harness unificado funcional, dez suítes registradas,
goldens versionados, fallback local e integração com Langfuse. A suíte hermética
do harness passa com nove testes. A divergência está na semântica operacional:

- `python -m core.eval` sempre termina com sucesso quando ao menos uma suíte
  roda, mesmo se métricas ficarem abaixo dos pisos descritos, casos falharem ou
  `presence_regression=true`;
- apenas o cenário em que todas as suítes são puladas retorna exit code não zero;
- matching documenta pisos para MRR, recall e hard negatives, mas não executa
  esses critérios nem possui limite aceito para `noise`;
- extraction calcula um booleano de regressão contra baseline, mas o harness
  apenas o imprime;
- triage exige avaliação “antes de mergear” em sua docstring, porém não declara
  threshold bloqueante;
- as demais suítes produzem métricas sem contrato explícito de aprovação;
- falha de infraestrutura, erro de task e degradação de qualidade podem virar o
  mesmo score baixo e não possuem status operacional distinto;
- runs não registram commit, dirty state, hashes dos datasets, modelos, flags ou
  versão do manifesto;
- `scripts/eval_report.py` compara qualquer run com o mesmo nome de métrica,
  mesmo quando corpus, número de casos, modelo ou configuração mudaram;
- direção de métricas é parcialmente hardcoded no relatório e não cobre, por
  exemplo, `noise` ou booleanos em que `false` é o resultado correto;
- com credenciais Langfuse presentes, até runs limitadas de debug são publicadas
  implicitamente, salvo uso de `--no-push`; e
- CI testa o código do harness, mas não executa gates externos nem deixa claro
  que esses gates dependem de ambiente, custo e serviços reais.

No workspace local havia 151 JSONs ignorados em `eval_results/`: 138 no formato
unificado, 27 com backend Langfuse e 111 locais, ocupando cerca de 14 MB. Eles são
úteis como evidência de uso do harness, mas não provam comparabilidade porque o
payload atual não captura o contexto necessário. Nenhum desses resultados será
versionado por esta spec.

## 2. Resultado pretendido

Toda avaliação deve declarar claramente uma de duas intenções:

1. **run:** diagnóstico ou experimento; produz métricas e evidência, mas nunca
   bloqueia por qualidade; e
2. **gate:** verifica critérios previamente aceitos, produz decisão objetiva e
   retorna exit code não zero quando reprova ou não consegue concluir.

Uma pessoa técnica deve conseguir responder, a partir de um resultado oficial:

- qual commit e configuração foram avaliados;
- qual versão dos casos e critérios foi usada;
- quais serviços e modelos participaram;
- se a rodada é comparável a outra;
- se passou, reprovou, foi pulada ou falhou operacionalmente; e
- qual mudança exigia aquele gate.

## 3. Princípios

1. **Métrica não é gate.** Um número só bloqueia quando possui direção,
   threshold, escopo e autoridade aceitos.
2. **Não codificar o comportamento atual como qualidade por conveniência.**
   Thresholds vêm de regra, golden curado ou decisão registrada.
3. **Erro operacional não é regressão de modelo.** Ambos impedem aprovação, mas
   devem ter estados e diagnósticos diferentes.
4. **Comparabilidade é explícita.** O relatório não compara runs incompatíveis.
5. **Goldens e baselines aceitos são versionados; outputs gerados não.**
6. **Langfuse é o histórico oficial de runs publicadas.** JSON local continua
   sendo fallback e ferramenta de debug.
7. **Gates externos são proporcionais à mudança.** Não executar `all` por
   ritual quando apenas uma responsabilidade mudou.
8. **Nenhum gate implícito de custo.** Publicação e chamadas externas devem ser
   deliberadas e identificáveis no comando.

## 4. Taxonomia das suítes

### 4.1 Estado inicial aprovado

| Suíte | Estado | Pode bloquear após esta execução? | Razão |
|---|---|---:|---|
| `matching` | candidata a gate | sim, depois de completar o contrato | critérios aceitos; falta julgar integralmente o top-8 e obter rodada aprovada |
| `extraction` | gate | sim | baseline aceito aplicado; golden curado e completude obrigatórios |
| `rag` | diagnóstica | não | métricas existentes, sem threshold atual aceito |
| `writing` | diagnóstica | não | juízes e métricas úteis, sem contrato bloqueante |
| `writing_v2` | experimental | não | contém limitações F0 e métricas sentinela indisponíveis |
| `triage` | diagnóstica obrigatória para mudanças de triagem | não | a exigência de execução existe, mas não há threshold aceito |
| `opportunity_type` | diagnóstica | não | golden pequeno e sem threshold aceito |
| `profile_extractor` | diagnóstica | não | sem threshold aceito |
| `reranker` | diagnóstica/opcional | não | backend produtivo pode estar desligado e extra local é opcional |
| `structurer` | diagnóstica | não | sem threshold aceito e provider configurável |

“Obrigatória” para uma suíte diagnóstica significa anexar evidência à mudança;
não permite ao CLI inventar aprovação ou reprovação.

### 4.2 Promoção futura a gate

Uma suíte só pode virar gate bloqueante quando declarar:

- corpus/golden autoritativo e proprietário;
- conjunto mínimo de casos e política para casos omitidos;
- métricas e direção (`higher_is_better`, `lower_is_better` ou booleano
  esperado);
- threshold ou invariantes aceitos;
- tolerância a não determinismo, quando houver;
- configuração suportada e prereqs verificáveis;
- política para falha de task/evaluator;
- trigger de execução; e
- custo e ambiente autorizados.

Essa promoção exige mudança explícita no manifesto versionado e revisão humana;
não pode acontecer automaticamente a partir do “melhor resultado histórico”.

## 5. Contrato dos modos

### 5.1 Run diagnóstico

Comando pretendido:

```bash
python -m core.eval run <suite> [--limit N] [--publish]
```

- roda qualquer suíte registrada;
- default local, sem publicação implícita;
- aceita subconjunto, dirty tree e overrides;
- marca o resultado como diagnóstico e não calcula decisão de merge;
- `--publish` só aceita uma rodada completa e envia ao Langfuse quando as
  credenciais estão presentes; e
- erros de execução aparecem no status, mesmo quando outros casos continuam.

Compatibilidade: a forma atual `python -m core.eval <suite>` pode permanecer
temporariamente como alias de `run`, com aviso de depreciação. `--no-push`
permanece durante a migração, mas o default novo já é local.

### 5.2 Gate

Comando pretendido:

```bash
python -m core.eval gate matching --publish
python -m core.eval gate extraction --publish
```

- só aceita suítes classificadas como gate e com contrato completo;
- não aceita `--limit`;
- exige todos os prereqs e o número esperado de casos;
- qualquer task/evaluator error torna o gate `error`, não um score de qualidade;
- aplica os critérios versionados;
- retorna `0` somente para `passed`;
- retorna não zero para `failed`, `error`, `skipped` ou resultado incompleto; e
- uma decisão oficial deve ser publicada no Langfuse. Sem Langfuse, o comando
  pode validar localmente, mas o manifesto marca a decisão como não publicada e
  ela não substitui o registro oficial exigido pelo fluxo de merge/release.

`all` continua disponível apenas para runs diagnósticos. Gates são selecionados
por trigger e responsabilidade, não por varredura indiscriminada.

## 6. Manifesto reproduzível

Todo output local e toda run publicada deve incluir um manifesto sem segredos:

| Campo | Conteúdo mínimo |
|---|---|
| `schema_version` | versão do payload/manifesto |
| `intent` | `run` ou `gate` |
| `status` | `diagnostic`, `passed`, `failed`, `error` ou `skipped` |
| `suite` | nome e versão/identificador da definição |
| `git` | commit, branch, dirty state e hash do diff quando aplicável |
| `dataset` | paths, hashes SHA-256, número esperado/carregado e ids omitidos |
| `criteria` | métricas, direção, thresholds e versão do baseline |
| `runtime` | Python, versão do pacote e backend local/Langfuse |
| `models` | nomes de modelos/providers efetivamente usados por tier ou juiz |
| `config` | allowlist de flags que mudam o resultado, nunca chaves/URLs secretas |
| `execution` | início/fim UTC, duração, modo completo/limitado e contagem de erros |
| `results` | agregados, avaliações da run e resultados por caso |
| `publication` | URL/identificador Langfuse quando publicado |

O hash do dataset define identidade, não qualidade. Alterar um golden cria uma
nova versão comparável apenas depois de revisão; não reescreve o significado de
runs anteriores.

## 7. Autoridade e armazenamento

### 7.1 Versionado no Git

- `eval_data/golden/`: datasets e goldens autoritativos;
- manifesto de critérios/baselines aceitos, em path único definido durante a
  implementação;
- definição das suítes e evaluators;
- testes herméticos do harness e dos critérios; e
- documentação do trigger operacional.

Os casos de escrita hoje em `tests/fixtures/eval_cases*.json` devem ser
reclassificados: se forem goldens de avaliação, migram para `eval_data/golden/`
com referências atualizadas; fixtures puramente de teste permanecem em `tests/`.

### 7.2 Não versionado

- `eval_results/*.json`;
- traces, respostas completas e relatórios locais; e
- artefatos de bake-off ou avaliação em progresso ainda não aceitos.

O histórico oficial de runs publicadas vive no Langfuse. Uma decisão durável
derivada de experimento entra no Git como critério, baseline ou spec — nunca como
um dump integral de resultados.

### 7.3 Artefatos sem consumidor atual

Os antigos `eval_data/golden/compliance_monitor.json` e
`tests/fixtures/eval_investor_match.json` possuíam resultados históricos locais,
mas nenhuma suíte registrada ou consumidor atual fora do histórico. Foram
preservados em `eval_data/historical/`, com sua condição documentada em
`eval_data/README.md`; não governam os pipelines atuais.

Os goldens `finep_relaxed`, `finep_independent` e `fapesp` possuem consumidores
em benchmarks atuais e permanecem como datasets diagnósticos.

## 8. Critérios iniciais dos gates candidatos

### 8.1 Matching

Critérios já aceitos e preservados:

- média de MRR ≥ 0,60;
- média de recall@10 ≥ 0,55; e
- todos os hard negatives de elegibilidade corretos.

Decisão aceita em 2026-07-15: falsos positivos não fazem parte normal da lista.
O contrato distingue:

- `false_positives_at_8`: somente resultados explicitamente julgados
  irrelevantes; o limite é exatamente zero; e
- `unjudged_at_8`: resultados ainda sem julgamento. Não são chamados de falsos
  positivos, mas o limite também é zero para que o gate não aprove ambiguidade.

Antes de ativar o gate, a execução deve:

1. classificar todo resultado exibido no top-8 como relevante, neutro defensável
   ou irrelevante confirmado;
2. fixar o número/ids esperados de casos de ranking e hard negatives;
3. tratar oportunidade ausente, snapshot vazio e erro de embedding como erro de
   execução;
4. registrar `AS_OF`, `MIN_AFFINITY`, dimensões/modelo de embedding e hashes dos
   dois goldens no manifesto; e
5. satisfazer simultaneamente MRR, recall, hard negatives, zero falsos positivos
   confirmados e zero resultados sem julgamento.

Até esses quatro pontos estarem concluídos, `matching` permanece candidata e
não pode se autodeclarar aprovada.

### 8.2 Extraction

Critério já aceito e preservado:

- média de `presence_accuracy` ≥ 0,95, equivalente a
  `presence_regression=false`.

Antes de ativar o gate, a execução deve:

1. proibir fallback silencioso para `extraction_draft.json` em modo gate;
2. exigir todos os casos do golden curado e reportar raws ausentes;
3. tratar falha de extração/evaluator como erro operacional; e
4. registrar modelo/provider, `DECISION_FIELDS`, hash do golden e fonte dos raws.

`value_correctness` e `evidence_faithfulness` continuam diagnósticas até que
tenham thresholds aceitos; não entram implicitamente na decisão.

## 9. Triggers operacionais

| Mudança | Evidência mínima |
|---|---|
| `match_v3`, elegibilidade, company chunks, embeddings de match ou regra WIKI consumida pelo funil | run `matching` completa; gate somente após promoção do contrato |
| schema/extrator de edital ou `DECISION_FIELDS` | gate `extraction` publicado |
| retriever, chunker, contextual retrieval ou RAG | run `rag` completa e publicada |
| agente/tools/prompts de escrita | run `writing` ou `writing_v2` aplicável |
| triagem/extração da Descoberta | run `triage` e/ou `opportunity_type` aplicável |
| extrator de perfil | run `profile_extractor` |
| reranker | run `reranker` no backend alterado |
| structurer ou regras WIKI correspondentes | run `structurer` |
| apenas UI, docs ou código hermético sem consumidor de IA afetado | testes proporcionais; nenhum eval externo por padrão |

Uma mudança que altera o golden não pode usar a mesma run para justificar
simultaneamente o novo critério e a aprovação do pipeline sem revisão explícita.

## 10. CI, merge e release

- CI padrão continua hermético: ruff, pytest, frontend e leak-test local;
- testes do harness devem provar exit codes, statuses, manifesto, completude e
  comparabilidade sem rede;
- gates com LLM/DB reais não entram silenciosamente no job padrão enquanto
  secrets, custo e ambiente não estiverem formalmente disponíveis;
- o registro oficial pode ser executado manualmente antes do merge/release e
  anexado à revisão; e
- um workflow manual futuro só pode ser criado depois de confirmar secrets,
  workspace de eval, limites de custo e política de retenção. Esta spec não
  presume essa infraestrutura externa.

## 11. Fora de escopo

- mudar prompts, modelos, ranking, retrieval, agentes ou dados de produto;
- criar novos evaluators para aumentar cobertura;
- inventar thresholds para as oito suítes diagnósticas;
- versionar os 14 MB de resultados locais existentes;
- publicar automaticamente todos os experimentos;
- configurar secrets ou projetos Langfuse/GitHub externos;
- comparar providers ou escolher modelos; e
- transformar avaliação em roadmap de produto.

## 12. Plano de execução

### Etapa 1 — Contrato e manifesto

1. estender `Suite` com classificação, critérios, direção de métricas e versão;
2. criar manifesto sanitizado e determinístico;
3. distinguir status de qualidade, erro operacional e skip;
4. preservar leitura de resultados antigos sem tratá-los como comparáveis; e
5. cobrir tudo com testes herméticos.

### Etapa 2 — CLI e publicação

1. introduzir `run` e `gate` com alias temporário para o CLI atual;
2. tornar publicação explícita;
3. implementar exit codes do gate;
4. impedir gate limitado/incompleto; e
5. anexar o manifesto à run Langfuse e ao JSON local.

### Etapa 3 — Gates candidatos

1. completar o contrato de matching com zero falsos positivos e cobertura total
   de julgamentos no top-8;
2. tornar extraction estrito quanto a golden e raws;
3. ativar apenas os gates completos; e
4. manter as demais suítes diagnósticas.

### Etapa 4 — Dados, relatório e runbook

1. separar goldens de fixtures de teste;
2. classificar datasets sem consumidor atual;
3. fazer o relatório comparar somente manifests compatíveis e respeitar direção
   de cada métrica;
4. documentar triggers, comandos, prereqs, custo e publicação; e
5. reconciliar `AGENTS.md`, `docs/architecture.md`, `.env.example` e CI.

## 13. Reversibilidade

- o CLI atual permanece como alias durante a migração;
- o schema de resultados recebe versão e mantém parser tolerante ao legado;
- `eval_results/` continua ignorado e intocado;
- nenhuma suíte diagnóstica perde task, evaluator ou golden por esta mudança;
- gates só são ativados após contrato completo; e
- mudanças externas de CI/Langfuse ficam fora desta entrega até autorização.

## 14. Validação

- `git diff --check`;
- `ruff check` nos arquivos Python alterados;
- `pytest tests/test_eval_harness.py` e testes novos de CLI/manifesto;
- testes de exit code para passed, failed, error, skipped e suíte não-gate;
- teste de sanitização que impeça chaves e URLs secretas no manifesto;
- teste de hash/contagem/omissões de dataset;
- teste de comparabilidade e direção de métricas no relatório;
- dry-run local sem credenciais externas;
- execução real apenas dos gates afetados, com ambiente autorizado, quando seus
  contratos estiverem completos; e
- `git status --short` confirmando que resultados e artefatos locais protegidos
  não entraram no diff.

## 15. Critérios de conclusão

O eixo estará concluído quando:

1. run diagnóstico e gate tiverem comandos e exit codes distintos;
2. todo resultado novo possuir manifesto reproduzível e sanitizado;
3. Langfuse receber apenas runs publicadas deliberadamente;
4. o relatório recusar comparação incompatível;
5. matching e extraction só bloquearem depois de contratos completos;
6. nenhuma outra suíte bloquear sem threshold aceito;
7. erro operacional, reprovação, skip e diagnóstico forem distinguíveis;
8. goldens, fixtures e artefatos sem consumidor tiverem autoridade clara;
9. triggers e prereqs estiverem documentados em uma única fonte operacional;
10. CI hermético continuar sem depender de secrets/LLMs; e
11. nenhum pipeline avaliado tiver comportamento alterado por este trabalho.

## 16. Resultado da execução

Implementado:

- CLI explícita `run`/`gate`, publicação opt-in e alias temporário da sintaxe
  anterior;
- manifesto v1 sanitizado com identidade Git, hashes e casos dos datasets,
  modelos/configuração, critérios, erros, status e publicação;
- estados `diagnostic`, `passed`, `failed`, `error` e `skipped`, com exit codes
  distintos;
- gate de extraction com piso `presence_accuracy >= 0,95`, golden estrito e
  raws históricos FAPESP reproduzíveis;
- matching classificado como candidato, com cinco critérios aceitos: MRR,
  recall, hard negatives, zero falsos positivos confirmados e zero resultados
  sem julgamento no top-8;
- goldens de escrita movidos para `eval_data/golden/` e corpora sem consumidor
  movidos, sem exclusão, para `eval_data/historical/`; e
- relatório limitado a runs de manifesto compatível e à direção declarada de
  cada métrica.

Evidência local de 2026-07-15, não publicada:

- matching v1: 11/11 casos, sem erro; MRR `0,673`, recall@10 `0,5237`, antiga
  métrica ambígua de noise@8 `4,5` e hard negatives `3/3`. A métrica v1 contava
  todo não rotulado como falso positivo e foi substituída pelo contrato v2;
- matching v2: 11/11 casos, sem erro; MRR `0,673`, recall@10 `0,5237`, falsos
  positivos confirmados `0`, resultados ainda sem julgamento no top-8 `4,5` em
  média e hard negatives `3/3`. O zero de falsos positivos não aprova a suíte,
  porque os não julgados e o recall ainda reprovam seus critérios;
- extraction, primeira rodada: `error` por três raws FAPESP omitidos pelo índice
  do snapshot mais recente;
- extraction após compor snapshots bronze históricos: 10/10 casos, sem erro,
  status `failed`; presence accuracy `0,8665` contra piso `0,95`, value
  correctness `0,6584` e evidence faithfulness `0,95`.

Os JSONs dessas rodadas permanecem ignorados em `eval_results/`. Os resultados
reprovados demonstram o gate; não autorizam mudanças no pipeline de produto.
