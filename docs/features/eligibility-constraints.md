# Spec — Estruturar `eligibility_constraints` na wiki page (elegibilidade dura no match)

Status: **proposta** · 2026-06-21 · escopo: 1 campo, aditivo, soft.

## Problema (preciso)

A elegibilidade dura (região / idade da empresa / faturamento) **já está na wiki page**, mas como **prosa** dentro de `key_requirements` — não como o campo **tipado** `eligibility_constraints` que o schema reserva. Consequência medida (31 cards de prod, 28 ricos `source=etl_process` com `objective`+`key_requirements`): **0 carregam `eligibility_constraints`**. Por isso:

- **Stage 1** (determinístico) não parseia prosa → a dimensão `_score_elegibilidade_dura` retorna `None` (dormente) → **gate cego** a região/idade/faturamento.
- **Stage 2** (LLM) recebe `key_requirements[:3]` truncado e o prompt só pede score **temático** → também ignora elegibilidade.

Isto **não contradiz** a filosofia "LLM wiki" (Karpathy): o match continua lendo a wiki, não texto bruto. O gap é um **passo de estruturação faltante na ingestão** — materializar um campo que parou na prosa. O consumo (match) **não muda**; os scorers (`_score_region/_company_age/_revenue` em [hybrid_match_service.py](core/services/hybrid_match_service.py)) já existem e o perfil já tem o par (`uf`/`ano_fundacao`/`faturamento_anual`, adicionados em 2026-06-21).

## Decisão de design — Opção 2

Rodar a extração **tipada** (`EditalExtractor` / [domain/edital_extraction.py](domain/edital_extraction.py)) e gravar **só** `eligibility_constraints` na wiki page. Escolhido sobre estender o prompt de síntese livre porque elegibilidade é onde NÃO se quer alucinação: o contrato `Extracted[T]` com **abstenção** (`absent` quando o texto não afirma) + **`evidence` verbatim** é a salvaguarda certa. Mantém-se **soft** (re-rank, nunca elimina — o scorer já é assim) e **aditivo** (um campo novo; sem fonte determinística concorrente → zero conflito de precedência).

## Decisões pinadas

**D1 — Produtor / seam.** A extração roda no passo de silver que já produz o card rico (`source=etl_process`, em [pipeline/etl_process.py](pipeline/etl_process.py) — confirmar a função exata na impl) **ou** como passo de enriquecimento sobre a wiki page. Recomendado: no silver, reusando o **texto já em mãos** (que gera `objective`/`key_requirements`) e o **cache por hash** (espelhar `.enrichment_cache.json`; não re-chamar LLM em edital inalterado). Gravar **só** `eligibility_constraints` — preserva o resto do card. Pular os `metadata_only` (sem texto → nada a extrair).

**D2 — Schema (doc-first, autoritativo).** Declarar `eligibility_constraints` como campo **synthesized** da wiki page no **WIKI.md** §4/§8.1 (estrutura: `[{type, description, evidence}]`, `type ∈ {region, company_age, revenue, cnae, consortium}`). Atualizar [core/kg/wiki_schema.py](core/kg/wiki_schema.py) e manter [tests/test_wiki_schema_consistency.py](tests/test_wiki_schema_consistency.py) verde. Mudança de regra vai no doc, não no código.

**D3 — Plano de eval (load-bearing).**
- **Extração**: já gate-able — `eval_data/golden/extraction.json` (10 casos) **já cobre** `eligibility_constraints`. Confirmar cobertura dos 3 tipos suportados (region/company_age/revenue); completar o golden se faltar tipo.
- **Match (o crux)**: a suíte `matching` (`tests/fixtures/eval_matching.json`) **não tem** caso onde a elegibilidade é o discriminador → ligar a dimensão seria **invisível** ao eval. **Adicionar ≥1 golden case**: perfil hard-inelegível (ex.: empresa SP × edital com `region` só-NE; ou empresa 10 anos × `company_age` "até 4 anos") onde o esperado é o edital **cair** no ranking vs. um par elegível. Sem esse caso, o ganho fim-a-fim não é mensurável nem gate-ável.

**D4 — Soft + HITL.** Re-rank apenas; nunca eliminar (já garantido pelo scorer: `None`→omitido, `0.5` neutro quando perfil sem o par). Aflorar a `evidence` verbatim ao usuário ("este edital exige sede no NE — *trecho*") = **Fase 2 / frontend**, fora desta spec.

**D5 — Rollout.** Aditivo + soft → **sem flag**; basta rebuildar o KG para repopular as wiki pages. Medir o **teto endereçável**: % do catálogo (41 editais) que de fato carrega constraint dura — decide se vale a Fase 2 (riqueza geral do card) depois.

## Plano de implementação (PRs)

1. **PR1 — schema**: WIKI.md + `wiki_schema.py` declaram `eligibility_constraints`; validator verde. (sem comportamento, destrava o resto)
2. **PR2 — golden de match**: caso(s) de elegibilidade em `eval_matching.json` + rodar `core.eval matching` para registrar o baseline COM a dimensão ainda dormente (deve permanecer estável). Estabelece a régua.
3. **PR3 — produtor**: wiring do extrator no silver (D1) + cache; popula `eligibility_constraints`. **GATE**: `core.eval extraction` (qualidade da extração) **e** `core.eval matching` (o golden de D3 melhora; nada mais regride).

## Fora de escopo (Fase 2+)

- Estruturar os demais campos de decisão (`themes`/`trl_range`/`mechanism`) a partir do texto onde o metadado é fraco (precisa de regras de precedência).
- Tipos de constraint além de region/company_age/revenue (cnae/consortium) no scoring.
- Campo `parceria_ict` no perfil (lado-empresa) e surfacing HITL da evidência.

## Riscos

- **Eval cego se PR3 mergear antes do golden de D3** → ordem dos PRs é dura (2 antes de 3).
- **Extração errada de constraint** esconde edital válido ou surfa inelegível → mitigado por abstenção + soft + evidence (auditável); nunca hard-gate nesta fase.
- **Teto baixo**: se poucos editais têm constraint dura, o ganho é pequeno — D5 mede isso antes de investir na Fase 2.
