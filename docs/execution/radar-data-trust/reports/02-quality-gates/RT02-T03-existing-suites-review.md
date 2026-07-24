# RT02-T03 — Revisão leve das suítes existentes

**Status:** `passed`
**Plano:** `docs/execution/radar-data-trust/plans/02-quality-gates/RT02-T03-existing-suites-review.md`
**Branch/commit-base:** `codex/radar-data-trust-02-t03` sobre `37f34a74d`
**Commits:** `<preenchido após o commit único>`
**Implementador/modelo:** claude-sonnet, worktree isolado

## Realizado

Para as 7 suítes do escopo (`triage`, `structurer`, `opportunity_type`, `rag`,
`explore`, `reranker`, `profile_extractor`), todas hoje dependiam do default
implícito `classification="diagnostic"` do dataclass `Suite`
(`src/radar/core/eval/harness.py:140`). Tornei a classificação explícita em
cada `Suite(...)`, uma linha por módulo, sem alterar `task`/`evaluators`/golden/
produtor/prereqs e sem adicionar `criteria`/threshold:

- `src/radar/core/eval/triage.py`
- `src/radar/core/eval/structurer.py`
- `src/radar/core/eval/opportunity_type.py`
- `src/radar/core/eval/rag.py`
- `src/radar/core/eval/explore.py`
- `src/radar/core/eval/reranker.py`
- `src/radar/core/eval/profile_extractor.py`

Confirmei carga via `registry.py` (as 12 suítes registradas carregam, incluindo
as 7 do escopo + as 5 fora do escopo) e execução/prereq honesto por suíte
(tabela abaixo). Golden contado por leitura direta do JSON (não pelo `run`, que
já filtra por prereq).

### Tabela por suíte

| Suíte | Classificação (após esta task) | Tamanho do golden | Execução nesta revisão | Nota de representatividade |
|---|---|---:|---|---|
| `triage` | `diagnostic` (agora explícita) | 122 casos (`data/evaluation/golden/triage.json`); 13 ainda com `review: true` (rotulagem aguardando palavra final do fundador, por docstring do módulo) | `[skip]` honesto: `requer OPENAI_API_KEY ou GEMINI_API_KEY (triagem)` — worktree limpo, sem `.env` | Contagem mede o corpus (DOU+Tavily 2026-06-10), não representatividade — spec-mãe §4.1. §4.1 já lista 122 como o estado inicial; nenhuma fragilidade material apontada que justifique estratificar agora. |
| `structurer` | `diagnostic` (agora explícita) | 12 casos | `[skip]` honesto: `requer OPENAI_API_KEY` | Idem §4.1 (12 é o valor do snapshot inicial). Golden pequeno; nenhuma fragilidade apontada na spec-mãe que justifique ampliar/estratificar nesta task. |
| `opportunity_type` | `diagnostic` (agora explícita) | 6 casos | `[skip]` honesto: `requer OPENAI_API_KEY ou GEMINI_API_KEY (extrator)` | Idem §4.1 (6 é o valor do snapshot inicial). Golden bem pequeno (3 classes: edital/desafio/programa) — é uma fragilidade de tamanho, mas a spec-mãe não a aponta como material nem esta revisão gera medição nova que a justifique; registro como observação, não como estratificação executada. |
| `rag` | `diagnostic` (agora explícita) | 28 queries (`finep.json`, fonte default `EVAL_RAG_SOURCE=finep`); há golden alternativo `fapesp.json` com 15 queries, não coberto pelo default | `[skip]` honesto: `requer SUPABASE_URL+SERVICE_KEY (retrieval em pgvector)` — worktree limpo, sem `.env` | Não listada em §4.1 (fora do snapshot inicial do programa). Contagem cobre só a fonte FINEP por padrão; FAPESP existe mas exige `EVAL_RAG_SOURCE=fapesp` explícito — não é gap desta task, é fato do produtor atual. |
| `explore` | `diagnostic` (já era "2" de `version`, mas `classification` era implícita — agora explícita) | 4 casos (`finep-745-itens-financiaveis`, `fapesc-31-2026-admissibilidade`, `barn-verticais`, `barn-tese`) | **Rodou de verdade**, rota hermética (sem `EVAL_EXPLORE_CONNECTED`, sem creds): `mean_route_accuracy = 1.0000` | Listada em §4.1 com 4 casos — igual ao medido. 4 casos é propositalmente pequeno (motivadores curados do NotebookLM); modo conectado (E2E real) segue opcional e não coberto nesta revisão. |
| `reranker` | `diagnostic` (agora explícita) | 20 casos | **Rodou de verdade**, backend `cross-encoder` local (sem API key, pesos já em cache): `mean_top1_accuracy = 1.0000`, `mean_ndcg_3 = 0.9312` | Não listada em §4.1. Cobre só o backend ativo por env (`RERANK_BACKEND`); se o backend de produção for outro, este golden não o exercita — fato do produtor, não desta revisão. |
| `profile_extractor` | `diagnostic` (agora explícita) | 15 casos | `[skip]` honesto: `requer OPENAI_API_KEY` | Não listada em §4.1. Golden verifica só campos presentes no `expected_output` de cada caso (don't-care no resto) — contagem de casos não equivale a cobertura de campos do `CompanyProfile`. |

Confirmadas (fora do escopo, apenas citação):

- `extraction`: `classification="gate"` já explícita (`src/radar/core/eval/extraction.py:182`) — não tocada.
- `matching`: `classification="candidate"` já explícita (`src/radar/core/eval/matching.py:246`) — não tocada.
- `relevance_shadow`: `classification="diagnostic"` já explícita (`src/radar/core/eval/relevance_shadow.py:1281`) — não tocada.
- `writing_v2`: `classification="experimental"` já explícita (`src/radar/core/eval/writing.py:423`) — não tocada.
- `writing` (a suíte plain, `name="writing"`, linha 433 do mesmo módulo): **achado** — ao contrário do que o plano e a task assumem ("já explícitas"), esta suíte **não** tem `classification=` explícito, dependendo do mesmo default implícito que motivou esta task. Como `writing.py` está fora do escopo autorizado para eu tocar (`writing`/`writing_v2` listadas nas exclusões), **não editei** — registro aqui para a governança decidir (fix de uma linha, mesmo padrão desta task, sem mudar valor efetivo).

## Divergências e decisões

1. **Local do relatório:** o plano (`RT02-T03-existing-suites-review.md`, linha 41)
   instrui explicitamente "não criar `reports/02-*` aqui — T05/governança criam".
   A task recebida para esta execução, no entanto, pede este relatório
   individual neste caminho, seguindo o mesmo padrão usado por toda task de
   `00-relevance` e `01-provenance` (`reports/<eixo>/<TASK-ID>.md`). Segui a
   instrução direta e mais específica desta execução (que é quem me deu a
   tarefa) e criei o relatório; deixo a divergência registrada para
   reconciliação — se a governança preferir, este arquivo pode ser
   consolidado/removido quando T05 rodar.
2. **`writing` sem `classification` explícita** — ver achado na tabela acima.
   Não corrigido por estar fora do escopo autorizado; recomendo o mesmo fix de
   uma linha (`classification="diagnostic"`) em `writing.py:433-443`, sem valor
   efetivo alterado, quando alguém tocar aquele módulo.
3. **Nenhuma estratificação executada.** Nenhuma suíte do escopo tem, na
   spec-mãe §4.1 ou na medição desta revisão, fragilidade material que
   justifique estratificar o golden agora. `opportunity_type` (6 casos, 3
   classes) é o mais frágil por tamanho, mas isso é uma observação, não uma
   medição que justifique ação — registrado, não executado, conforme o "Pare"
   do plano.
4. **Incidente operacional durante a investigação (não faz parte da entrega,
   registrado por transparência):** ao testar se as suítes efetivamente rodam,
   eu inicialmente `source`ei por engano o `.env` do checkout principal
   (`/Users/lucasborges/radar_editais/.env`), que está configurado com
   `ENVIRONMENT=production` e `SUPABASE_URL` de nuvem (não o Postgres local
   `:54322`). Isso fez a suíte `rag` rodar de fato contra dados de produção
   (retrieval + juiz de faithfulness), leitura apenas, sem escrita, mas com
   custo real de API e acesso a dados de produção — o que contraria a
   convenção conhecida de que eval roda local, nunca prod/cloud. Descartei
   esses resultados (não estão nesta tabela nem em nenhum artefato commitado;
   os arquivos em `eval_results/` gerados por essa rodada foram apagados antes
   de qualquer commit). Refeitos os testes em ambiente limpo (sem `.env`,
   worktree como está de fato hoje), que é o resultado reportado acima. Nenhum
   dado foi alterado; nenhuma credencial foi commitada. Sinalizo para que o
   dono do repositório saiba que isso ocorreu.

## Dados e migrations

- não aplicável.

## Validação

| Comando/verificação | Resultado |
|---|---|
| `PYTHONPATH=src pytest -q tests/unit` | `1321 passed, 2 skipped, 4 warnings` |
| `PYTHONPATH=src python -c "from radar.core.eval.registry import SUITES; print(sorted(SUITES))"` | `['explore', 'extraction', 'matching', 'opportunity_type', 'profile_extractor', 'rag', 'relevance_shadow', 'reranker', 'structurer', 'triage', 'writing', 'writing_v2']` — todas as 12 suítes carregam |
| `ruff check` (7 módulos do escopo) | `All checks passed!` |
| `git diff --check` | sem saída (sem whitespace issues) |
| `git diff 37f34a74d --stat` (antes do commit do relatório) | 7 arquivos, 1 inserção cada, `7 files changed, 7 insertions(+)` — só os módulos de eval do escopo |

## Pendências

- Recomendação (não executada): aplicar `classification="diagnostic"` explícito
  também em `writing.py:433` (suíte `writing` plain) — mesma correção desta
  task, fora do escopo autorizado aqui.
- Nenhuma estratificação de golden recomendada no momento; `opportunity_type`
  (6 casos/3 classes) é o candidato mais frágil se o proprietário quiser
  revisitar no futuro.

## Auditoria Codex

**Veredito:** pendente

- ...
