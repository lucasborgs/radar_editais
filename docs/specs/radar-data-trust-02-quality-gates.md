# Radar Data Trust 02 — Quality gates e cobertura de avaliação

**Status:** proposta para aprovação · **Data:** 2026-07-24
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)
**Contrato anterior:** [`radar-data-trust-01-provenance.md`](radar-data-trust-01-provenance.md)
**Ordem:** 02 · **Bloqueia:** alegações de cobertura e promoção de gates
**Perfis afetados:** operador e usuário técnico
**Impacto:** médio; avaliação, goldens e observabilidade de qualidade

---

## 1. Problema comprovado

A spec 01 entregou proveniência de ponta a ponta, mas a reconciliação final
(RT01-T13) mediu uma lacuna concreta: as evals do §10 da spec 01 (resolução de
locator, completude de proveniência por campo, faithfulness do trecho) existem
apenas como testes unit/integration ad-hoc por task — `git diff` da base do
programa contra `src/radar/core/eval/` é vazio. Ou seja: o **código** de
proveniência foi validado, mas o programa não produz uma **métrica agregada,
versionada e reproduzível** dessas propriedades.

O runtime já tem o instrumento certo: o harness unificado
(`src/radar/core/eval/harness.py`, registro em `registry.py`), com `Suite`
declarativa, `Criterion` versionado e `classification` (`gate | candidate |
diagnostic | experimental`). O que falta não é infraestrutura — é registrar as
suítes que faltam e tornar explícito o mapa de qualidade por camada.

Fotografia atual das classificações (início desta spec):

| Suíte | Classificação | Threshold aceito |
|---|---|---|
| `extraction` | `gate` | sim (baseline 0.95) |
| `matching` | `candidate` | proposto (mrr≥0.60, recall@10≥0.55), não aceito |
| `relevance_shadow` | `diagnostic` | não |
| `writing` / `writing_v2` | `experimental` | não |
| `triage`, `structurer`, `opportunity_type`, `rag`, `explore`, `reranker`, `profile_extractor` | `diagnostic` (default) | não |
| **proveniência (§10 da spec 01)** | **inexistente** | não |

Consequências:
- não há denominador para afirmar "cobertura de qualidade" por camada;
- proveniência não tem sequer sinal diagnóstico agregado;
- goldens pequenos (`extraction` 10, `structurer` 12, `opportunity_type` 6,
  `explore` 4) sustentam afirmações mais fortes do que seu tamanho permite; e
- não existe um sinal E2E que ligue as camadas (descoberta → gold → consumo).

## 2. Resultado pretendido

Ao fim desta spec, o programa deve poder responder, de forma reproduzível e
sem inventar régua:

1. qual a métrica agregada e versionada de cada camada de qualidade;
2. qual o sinal diagnóstico de proveniência (locator, completude,
   faithfulness) sobre um golden representativo;
3. qual a saúde ponta a ponta de um caminho E2E mínimo; e
4. quais suítes estão maduras o suficiente para uma futura promoção a gate —
   como **recomendação com baseline em mãos**, nunca como threshold decretado
   por esta spec.

Esta spec **produz medida e estrutura**; ela não decide números de corte.

## 3. Não-objetivos

- inventar qualquer threshold novo ou promover qualquer suíte a `gate` nesta
  passada (isso exige corpus e baseline aceitos pelo proprietário — invariante
  da spec-mãe §6.10);
- construir um segundo harness, runner, banco de métricas, dashboard ou serviço
  de eval — tudo reusa `core/eval/` e `registry.py`;
- alcançar cobertura exaustiva de casos; pré-beta usa uma fixture
  representativa por caso/origem;
- alterar prompts, modelos, matching, ranking, RAG ou qualquer consumidor;
- reescrever os goldens existentes ou seus produtores;
- rodar LLM real, banco remoto ou rede em testes sem autorização.

## 4. Comportamento atual e pretendido

**Atual:** `python -m radar.core.eval run <suíte>` roda o pipeline real e grava
`eval_results/*.json` com manifesto; `gate` só aplica critério aceito. As
suítes acima existem; proveniência e E2E não; o mapa por camada é implícito.

**Pretendido:** o mesmo comando passa a cobrir (a) uma suíte `provenance`
diagnóstica; (b) um sinal E2E diagnóstico; (c) uma revisão leve que confirma
que cada suíte existente roda, produz métrica estável e está classificada
corretamente; e (d) um mapa por camada documentado. Nenhum comportamento de
`run`/`gate` muda; nenhuma suíte nova bloqueia.

## 5. Invariantes

Herdados da spec-mãe e da spec 01, mais:

1. **Diagnóstico antes de gate:** uma suíte nasce `diagnostic`; só vira
   `candidate`/`gate` por decisão do proprietário com baseline aceito.
2. **Sem harness paralelo:** toda eval é uma `Suite` registrada em
   `registry.py`, reusando `core/*_eval.py`. Nenhuma infra nova.
3. **Sem threshold fabricado:** nenhuma métrica ganha número de corte sem
   corpus e baseline aceitos; recomendação de maturidade não é gate.
4. **Proporcionalidade pré-beta:** uma fixture representativa por caso/origem;
   nada de dezenas de variações redundantes.
5. **`run` nunca bloqueia; `gate` só aplica critério aceito** (contrato atual
   do harness, preservado).
6. **Medir não é mascarar:** lacuna medida (ex.: proveniência `stated=0` em
   editais legados) é entregável honesto, nunca contornada ou "consertada".
7. **Sem mudança silenciosa de consumidor:** avaliação observa o pipeline; não
   o altera. Mudança de IA dispara eval, não o contrário.

## 6. Mapa de camadas de qualidade

A spec formaliza (documenta, não cria) o mapa camada → suíte → classificação:

```text
Triagem/relevância   → triage, relevance_shadow           (diagnostic)
Aquisição/estrutura  → structurer                         (diagnostic)
Extração             → extraction                         (gate, 0.95)
Proveniência         → provenance                         (diagnostic, NOVA)
Recuperação/consumo  → rag, matching, explore, reranker   (diagnostic/candidate)
Perfil               → profile_extractor                  (diagnostic)
Escrita              → writing, writing_v2                (experimental)
E2E                  → e2e_health                         (diagnostic, NOVA)
```

Toda camada crítica deve ter **ao menos uma suíte que produz métrica**. Onde
faltar (proveniência, E2E), esta spec registra. Onde existir mas estiver
frágil, a revisão leve (§7.3) confirma que roda e mede — sem inflar o golden
além do necessário.

## 7. Escopo

### 7.1 Suíte `provenance` (a dívida da spec 01 §10) — NOVA, diagnóstica

Uma `Suite` que roda o caminho real de resolução (`evidence_resolver` +
projeção de proveniência do gold) sobre um golden representativo e agrega:

- **taxa de resolução de locator:** proporção de trechos que resolvem para
  `exact` / `document_only` / `unresolved`;
- **completude de proveniência por campo crítico:** proporção de fatos do
  escopo §3.1/§3.2 da spec 01 com estado factual e produtor;
- **faithfulness do trecho:** o `quote` resolvido é substring verbatim do
  bloco silver correspondente (reusa a checagem já existente).

Golden: um caso por tipo obrigatório da spec 01 §10.2 (trecho único/exato;
repetido em duas páginas; HTML sem página; valor normalizado; campo ausente;
registro legado sem silver). Fixture representativa, não exaustiva. Sem
threshold — `classification="diagnostic"`, `criteria=()`.

### 7.2 Sinal E2E `e2e_health` — NOVO, diagnóstico

Um caminho mínimo e determinístico descoberta→gold→consumo que produz sinais
de saúde (as camadas conectam; um fato sobrevive ponta a ponta com sua
proveniência), não uma matriz de casos. Diagnóstico; sem threshold. Roda contra
fixtures/banco local; nunca prod/rede/LLM real sem autorização.

### 7.3 Revisão leve das suítes existentes

Para cada suíte diagnóstica sem classificação/critério explícito (`triage`,
`structurer`, `opportunity_type`, `rag`, `explore`, `reranker`,
`profile_extractor`): confirmar que roda, produz métrica estável e está
classificada corretamente; corrigir classificação ausente; registrar o tamanho
do golden e se é representativo. Estratificação de golden só onde a spec-mãe
§4.1 apontou fragilidade material e a medição da própria suíte justificar —
não por princípio. **Nenhum threshold é adicionado.**

### 7.4 Mapa e recomendação de maturidade

Documento (em `reports/02-quality-gates/`) com o mapa da §6 preenchido pelos
resultados diagnósticos e uma **recomendação** de quais suítes têm baseline
suficiente para o proprietário considerar promoção futura a gate — explicitando
o baseline observado. Recomendação, não decisão.

## 8. Coexistência e compatibilidade

- Suítes novas são aditivas ao `registry.py` (uma linha cada);
- `run`/`gate`/manifesto/`eval_results` inalterados;
- nenhuma suíte nova bloqueia CI (todas `diagnostic`);
- goldens novos vivem em `data/evaluation/golden/` como os existentes;
- os consumidores (matching, RAG, Explorar, escrita) não mudam.

## 9. Rollout e rollback

1. Registrar suítes diagnósticas (nada bloqueia) → medir em shadow.
2. Documentar o mapa e o baseline observado.
3. Promoção a `candidate`/`gate` é passo POSTERIOR, fora desta spec, só com
   aceite do proprietário.

Rollback = remover a linha da suíte no `registry.py` e seu golden; nada
downstream depende delas (são diagnósticas). Nenhum dado produtivo é tocado.

## 10. Observabilidade

Cada suíte grava `eval_results/*.json` com manifesto (dataset, env/config,
versão) — mecanismo atual. A spec não adiciona telemetria nova; reusa o
manifesto para tornar as rodadas comparáveis.

## 11. Evals e critérios objetivos

Os "critérios objetivos" desta spec são de **conclusão da capacidade**, não de
qualidade do produto (que seria threshold — fora de escopo):

- as suítes `provenance` e `e2e_health` existem, registram e rodam localmente
  produzindo métrica agregada estável entre execuções;
- a revisão leve confirmou classificação e execução de cada suíte existente;
- o mapa por camada está preenchido com baseline observado;
- nenhuma suíte nova é `gate`; nenhum threshold foi inventado.

## 12. Plano de tasks (proposta de ordem; o Opus detalha)

A decomposição executável vive em
`docs/execution/radar-data-trust/plans/02-quality-gates/` e é a autoridade
sobre arquivos e ordem. Ordem lógica proposta:

| Task | Resultado |
|---|---|
| `RT02-T01` | golden representativo de proveniência (§10.2) — fixtures, sem código de suíte |
| `RT02-T02` | suíte `provenance` diagnóstica registrada (§7.1) |
| `RT02-T03` | revisão leve das suítes existentes: classificação, execução, tamanho de golden (§7.3) |
| `RT02-T04` | sinal E2E `e2e_health` diagnóstico (§7.2) |
| `RT02-T05` | mapa por camada + recomendação de maturidade + reconciliação (§7.4) |

Cada task é pequena, aditiva e auditável isoladamente; nenhuma inventa
threshold. Paralelismo seguro e dependências ficam a cargo do plano.

## 13. Critérios de conclusão

A spec pode ser marcada vigente quando:

1. `provenance` e `e2e_health` registradas, diagnósticas, rodando local com
   métrica agregada estável;
2. golden de proveniência cobrindo os casos obrigatórios da spec 01 §10.2, um
   por tipo;
3. revisão leve concluída (classificação/execução/tamanho registrados);
4. mapa por camada preenchido e recomendação de maturidade entregue como
   recomendação, sem promover gate;
5. nenhum threshold novo, nenhum harness paralelo, nenhum consumidor alterado;
6. suíte de testes e lint proporcionais verdes; documentação autoritativa
   reconciliada.

## 14. Autoridade e reconciliação

- `AGENTS.md` (seção de avaliação) e `docs/specs/evaluation-operations.md`
  permanecem autoritativos sobre o harness; esta spec adiciona suítes, não
  redefine o contrato de `run`/`gate`.
- Ao concluir, atualizar o status desta spec, a tabela §9 da spec-mãe e o mapa
  de camadas; não copiar estado implementado para múltiplos docs.
