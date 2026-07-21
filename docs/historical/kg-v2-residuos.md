# Spec — KG v2: resíduos de qualidade + ranking unificado

Status: **histórica, substituída** por [`v3-unified.md`](../specs/v3-unified.md) ·
registro de qualidade do hipergrado v2, não descrição do runtime atual.

> Continuação de [`kg-redesign.md`](kg-redesign.md) (8 PRs implementados). Esta spec trata o que o
> diagnóstico pós-implementação (2026-07-05) mediu como resíduo — nada aqui é falha de arquitetura;
> é profundidade/cobertura dos passes de qualidade e um bug de UX no radar.
>
> Pré-beta, sem usuários reais: **sem gates estatísticos nem testes exaustivos**. A suíte
> `python -m radar.core.eval matching` roda como sanity após cada passe de dados. O porquê de cada
> decisão fica documentado para reavaliação futura.

---

## Motivação (números medidos em 2026-07-05)

| # | Resíduo | Medida | Dano à proposta de valor |
|---|---|---|---|
| A | **Ranking não é decrescente no radar** | backend devolve editais e entidades em coleções separadas (`explore.py:156-160`); frontend renderiza agrupado por tipo (`page.tsx:797/813`) — programa com score alto aparece abaixo de edital com score baixo | Core UX do radar; visível a qualquer usuário |
| B | **Higiene deixou lixo** | TRL como Conceito em **2 grafias** (fan-in 10 + 5); LGPD como tema (fan-in 6); **52 pares** não-fundidos com cosseno > 0.90 (variantes singular/plural, "de/para", ordem) — cluster TIC em 6 grafias | "Vergonha visível" na ficha e afinidade falsa na margem |
| C | **Menções de programa não resolvidas** | 129 nós `kind=programa`, **111 names distintos**; "Mais Inovação Brasil" 5× curto + 2× por extenso; um programa chamado literalmente `"programa"` | Cards duplicados/fantasma; ficha rica do curado não é a encontrada |
| D | **Granularidade de frase** | **29% dos Conceitos têm 5+ palavras** ("agricultura de baixo carbono e uso eficiente de recursos"); compostos "X e Y" nunca casam cross-file. Editais do MESMO macro-tema compartilham em média **0,79 conceito** (31/72 pares compartilham zero) | A camada de Conceitos não funciona como tecido conectivo — limita explore/travessia (teto, não piso) |
| E | **Cobertura + regras rasas** | constraints em **6/178** Oportunidades; macro_temas em **73/178**; Estágio 0 existe mas opera faminto; regras de interpretação (bandas de porte FINEP, contrapartida porte×região, SUDAM/SUDENE, grupo econômico) não existem em lugar nenhum | O pilar "só o que você pode acessar" não é entregue; erro de elegibilidade é o mais caro do produto |

Diagnóstico completo (metodologia dos 3 testes: probe de sinônimos, compartilhamento condicionado
ao macro-tema, distribuição de comprimento) na conversa de 2026-07-05; script em scratchpad
(`vocab_diag.py`) — reproduzível.

## Decisões pinadas

| # | Decisão | Por quê |
|---|---|---|
| R1 | **Curado vence extraído, sempre** (precedência em qualquer conflito de metadado) | Curadoria é o canal humano de conhecimento; extração é aproximação |
| R2 | **Mundo aberto com status**: menção sem âncora curada é promovida a canônico com `status: promovido_auto`; enum `curado \| promovido_auto \| pendente`. A lista de não-curados é a **fila de trabalho do curador** | Curadoria crescerá com o tempo (decisão do usuário 2026-07-05); os 10 programas curados são semente, não universo; discovery trará novos |
| R3 | **Elegibilidade se apresenta como red flag / "não verificado", nunca como garantia**, até as regras curadas existirem e a cobertura ser total | `unknown` nunca elimina (herdado da kg-redesign); afirmar elegibilidade errada é o único erro quase-existencial do produto |
| R4 | **Regras de negócio como dado curado** (`data/curadoria/regras_elegibilidade.json`), não como código nem improviso do LLM | Mesmo padrão de investidores/programas: conhecimento determinístico versionável, consumido pelo avaliador e pelo prompt do veredito |
| R5 | **Desenvolvimento em paralelo, execução de dados serializada.** Agentes paralelos desenvolvem código + fixtures em worktrees; **nenhum agente roda passes sobre `data/knowledge_graph/` real** — os hipergrafos são arquivos locais (gitignored): dois passes concorrentes se sobrescrevem sem git para salvar. Execução real: sequência única B→C→D→E, backup por passe, sanity entre passes | Paraleliza o que é seguro (código), serializa o que é destrutivo (dados) |
| R6 | **Chave única de ordenação do radar = afinidade (soma MaxSim)**, comparável entre editais e entidades (mesmo motor desde PR6); desempate estável por id. Nenhum agrupamento por kind na ordenação | "Ranking decrescente independente do mecanismo" (decisão do usuário 2026-07-05). Pisos diferentes (`MIN_AGGREGATE_*`) seguem valendo como corte, não como ordenação |
| R7 | Termo atômico como alvo de extração: **≤3 palavras salvo nome próprio consagrado**; compostos "X e Y" viram dois Conceitos | É o fix de raiz do resíduo D; frase é rótulo, não vocabulário |

## PRs (ordem de importância de produto)

### PR-A — Ranking unificado decrescente no radar
**O quê:** fundir os resultados de `find_matching_editais` + `find_matching_entities` numa lista única ordenada por afinidade decrescente (R6), no backend (`explore.py` monta a lista; payload ganha lista única com `kind` por item, mantendo campos atuais por compatibilidade). Frontend renderiza intercalado (um map só decidindo `MatchedEditalCard` vs `MatchedEntityCard` por item). Aproveitar e ligar o attach/poll do veredito para cards `kind=programa` (o PR8.1 anotou "é só adicionar no attach/poll", mas na verdade programa exige **serializador próprio** — não tinha caminho de serialização; só ofertas de investimento tinham).
**Pronto quando:** radar exibe cards em score estritamente decrescente com kinds misturados; programa com score maior aparece acima de edital com score menor.

### PR-B — Higiene, segunda demão
**O quê:** duas frentes no `canonicalize_concepts.py`:
1. **Regras explícitas anti-classe-errada** no validador: faceta/métrica não é Conceito (TRL e variantes), citação legal não é Conceito (LGPD, "Lei nº …"), rótulo genérico não é Conceito ("programa", "tecnologia", "consultoria"). Lista de padrões + julgamento LLM para o resto.
2. **Fusão da banda > 0.90**: os ~52 pares near-duplicate passam por adjudicação LLM (mesmo conceito? → funde para a forma canônica, re-aponta arestas por id). Merges logados.
Re-embed ao final (cache invalida por hash).
**Pronto quando:** TRL/LGPD ausentes dos Conceitos; banda >0.90 zerada ou justificada no log; sanity estável.

### PR-C — Resolução de menções de programa
**O quê:** passe `resolve_programas.py`: (1) clusteriza os 111 names por similaridade (embedding + adjudicação LLM nos empates); (2) cada cluster resolve contra o registro curado — casa → link ao canônico curado (R1: metadado curado vence); não casa → promove canônico novo com `status: promovido_auto` e metadado mínimo (R2); (3) menções nos arquivos de edital viram referência (`pertence_a` → id canônico), não nós duplicados. Radar/ficha passam a exibir só canônicos. Descarte de lixo óbvio ("programa") via validador do PR-B.
**Pronto quando:** nenhum programa duplicado no radar; todo card de programa aponta para um canônico com status; fila de `promovido_auto` listável.

### PR-D — Granularidade atômica dos Conceitos
**O quê:** (1) prompt do `hyper_extractor` passa a exigir termo atômico (R7), com exemplos positivos/negativos; (2) passe `split_concepts.py` no corpus: os ~507 names com 5+ palavras são decompostos por LLM em termos atômicos (ou mantidos, se nome próprio); nós novos herdam dim/arestas do original; canonicalização (validador do PR-B) roda sobre o resultado; re-embed.
**Por quê depois de B:** o split gera nós novos que precisam do validador afiado; rodar antes seria higienizar duas vezes.
**Pronto quando:** % de names 5+ palavras < 10%; compartilhamento médio entre editais do mesmo macro-tema sobe (medir com o `vocab_diag`; sem meta numérica — registrar o delta); sanity estável.

### PR-E — Cobertura total + regras de elegibilidade curadas
**O quê:** três frentes:
1. **Cobertura**: rodar `extract_constraints.py` e o mapeamento de macro_temas no acervo inteiro (178 Oportunidades) — operacional, custo de API.
2. **Regras curadas** (R4): criar `data/curadoria/regras_elegibilidade.json` com o que for codificável hoje — bandas de porte (ME/EPP/média FINEP), tabela de contrapartida porte×região (se disponível nas fontes), interpretações padrão (receita = último exercício). `eligibility.py` consome as tabelas; prompt do veredito recebe o trecho relevante.
3. **Perfil**: conferir que os campos que as regras consomem (faturamento, forma jurídica, UF, data CNPJ) existem no CompanyProfile/onboarding; completar o que faltar.
**Pronto quando:** constraints e macro_temas ≥ 95% do acervo; Estágio 0 avalia sat/unsat (não só unknown) para perfil completo; veredito cita regra curada quando aplica red flag.

## Plano de paralelização (agentes)

```
Wave 1 (3 conversas paralelas, worktrees, Opus):
  Agente 1 → PR-A  (código puro backend+frontend; não toca dados)
  Agente 2 → PR-B  (script/validador + fixtures sintéticas; NÃO roda no corpus)
  Agente 3 → PR-C  (script resolução + fixtures; NÃO roda no corpus)

Wave 2 (após merge de B; paralela ao restante):
  Agente 4 → PR-D  (prompt + split script + fixtures; usa validador de B)
  Agente 5 → PR-E frentes 2-3 (regras curadas + eligibility.py + perfil — código,
             independe dos passes de dados)

Execução dos passes de dados (SERIALIZADA — R5, uma conversa ou manual):
  backup → B → sanity → C → sanity → D → re-embed → sanity → E.1 (cobertura) → sanity
```

- Modelo: **tudo Opus** — nenhum PR tem design em aberto (prompts de adjudicação/validação especificados aqui; dúvidas de design voltam para conversa de discussão, não se resolvem no PR).
- Cada conversa usa o template padrão apontando para esta spec + a seção do seu PR.
- Fixtures: cada script de passe aceita `--dry-run` e roda em fixture pequena no CI; o corpus real só é tocado na execução serializada.

## Fora de escopo (com dono futuro)

- **Recorrência temporal** ("PIPE reabre ~3x/ano, próxima janela em X") — alto valor, requer modelagem própria; candidata a spec futura.
- **Cumulatividade de mecanismos** (subvenção + Lei do Bem etc.) — depende das regras curadas amadurecerem.
- **Veredito para ICT** — aguarda ICT virar Oportunidade recomendável (parceria_pd em pé).
- **Meta numérica de compartilhamento de vocabulário** — deliberadamente sem meta: a dispersão genuína (cauda longa) é saudável; medimos o delta e julgamos.

## Previsto → Realizado

### PR-A — Ranking unificado decrescente + veredito de programa · realizado 2026-07-05

Branch `feat/kg-v2-residuos-pr-a` (2 commits). Sanity `python -m radar.core.eval matching`:
`recall@k 0.8334`, `noise 3.0`, exit 0 — baseline pré-existente (o PR-A não toca o
caminho pontuado do match; só adiciona um campo de display no `to_dict` e mexe na
ordenação/veredito do router, que o eval de matching não exercita).

**Commit 1 — ranking unificado (R6).**
- `EditalMatch.to_dict()` ganha `kind:"edital"` (entidades já tinham `kind`); cada
  item do radar passa a ser auto-descritível.
- Fusão feita no **front**: helper `mergeRadar(matchedEditais, matchedEntities)` em
  `frontdoor.ts` é o **único** lugar com a lógica de ordenação (afinidade decrescente,
  desempate estável por id string). `page.tsx` renderiza um `map` só (header único
  "Oportunidades com afinidade"), sem agrupamento por kind. Vale igual no turno fresco
  e na retomada (ambos chegam como duas listas; persistência intacta — "campos atuais
  por compatibilidade"). Interpretação de "explore.py monta a lista": o backend torna
  cada item auto-descritível; a ordenação canônica vive no helper.
- `explore.py` **parou de chamar `reorder_by_verdict`** no radar. **Divergência
  DELIBERADA do D9 da kg-redesign:** a geometria rankeia (afinidade é a chave única,
  R6); o veredito vira **sinalização** no card (red flag, R3), não posição.
  `reorder_by_verdict` continua definido (outros usos/testes), só não é mais chamado
  no funil do radar.

**Commit 2 — veredito de programa.**
- **Correção da anotação do PR8.1** ("é só adicionar no attach/poll"): programa **não
  tinha** caminho de serialização — só ofertas de investimento (`investment_offer_subgraph`).
  Precisou de serializador próprio.
- `hypergraph_catalog.programa_node()` (público, espelha `investment_offer`) +
  `match_verdict.programa_subgraph()` (nó do programa + conceitos ligados a ele, sem o
  hop pelo fundo). `serialize_for_verdict` ganha branch `kind=programa`;
  `attach_cached_verdicts_entities` generaliza por kind via `_ENTITY_VERDICT_RESOLVERS`
  (investidor→oferta, programa→programa; **ICT fica de fora** — parceiro sugerido, não
  Oportunidade recomendável). Front: poll de veredito inclui `kind=programa` (restore + send).
- Testes: fixture `PROGRAMA_CATALOG` + cobertura de subgraph/dispatch/attach. 14/14 verdes.

**Nuance de display registrada:** o anel dos cards mostra `score` (melhor cosseno); a
ordenação é por `affinity` (R6). A linha "🎯 affinity×10" fica estritamente decrescente,
mas o anel **%** pode não ser monotônico entre cards. Fora do escopo do PR-A mudar o que
o anel exibe (R6 não pede) — candidato a revisitar se confundir na prática.

**Não tocado:** os passes de dados (B→C→D→E) — PR-A é código puro. Nenhum script rodou
sobre `data/knowledge_graph/` real.

### PR-B — Higiene, segunda demão · realizado 2026-07-05

Branch `feat/kg-v2-residuos-pr-b` (empilhada sobre PR-A). **Code-only** (Wave 1): o
validador/merge foi reforçado + fixtures, **sem rodar sobre o corpus**. Sanity
`python -m radar.core.eval matching`: `recall@k 0.8334`, `noise 3.0`, exit 0 (baseline inalterado
— o passe não roda aqui). `ruff` limpo; `pytest tests/unit/test_canonicalize.py` 15/15.

**Frente 1 — descarte determinístico por classe errada** (`core/kg/canonicalize.py`).
- `anti_class_verdict(name)`: a "lista de padrões" da spec — **metrica** (TRL/readiness/
  "nível de maturidade tecnológica"), **legal** (LGPD, "Lei nº X", decreto/portaria
  numerados, Marco Civil), **generico** (rótulo nu: "programa", "tecnologia", "consultoria",
  "inovação", …). Casa só o inequívoco; composto legítimo ("tecnologia assistiva", "saúde
  digital") e falso-amigo ("Lei do Bem" = mecanismo, "marco regulatório de saneamento")
  passam incólumes — coberto por teste.
- Integrado como **pré-filtro** em `propose_validation`: os determinísticos entram no plano
  já como `descartar` e **não vão ao LLM** (mais barato e determinístico); o julgamento LLM
  fica só p/ o ambíguo. `_VALIDATION_SYSTEM` reforçado + categoria `metrica` no enum.

**Frente 2 — fusão da banda >0.90** (`core/kg/canonicalize.py`).
- `HIGH_CONF_MERGE=0.90` + `_variant_key` (deburr/lower, remove conectivo, singulariza
  plural simples, ordena tokens) + `_auto_variant_merges`: variantes triviais (singular/
  plural, "de/para", ordem) fundem **sem LLM** sob **gate duplo** (mesma chave E todo par com
  cosseno ≥ 0.90 → falso-positivo ~0; falso-negativo cai no LLM). `propose_merges` manda só
  o resto ao adjudicador. `_MERGE_SYSTEM` reforçado (variantes triviais SEMPRE fundem) e
  corrigido (o exemplo estava usando TRL, que a Frente 1 agora descarta → troquei por IA).
- CLI `propose-merges` reporta `N determinísticos (banda >0.90) + M por LLM` — os
  determinísticos dispensam `sample-merges`. Os grupos levam `auto:true` p/ auditoria.

**Re-embed:** é passo da execução serializada (não do código) — o cache de embedding
invalida por hash do texto; conceito renomeado/fundido gera texto novo → re-embed no próximo
`embed`. Nada a codar aqui.

**Sobre "banda >0.90 zerada" (pronto-quando):** é critério de EXECUÇÃO — verificável só
quando o passe rodar sobre o corpus real (R5, etapa serializada à parte). O código entrega o
mecanismo (auto-merge determinístico + prompt reforçado) e o log que reporta o resultado; a
zeragem/justificativa sai no `propose-merges` do passe real.

**Não tocado:** nenhum script rodou sobre `data/knowledge_graph/` real.

### PR-C — Resolução de menções de programa · realizado 2026-07-05

Commit próprio na branch única empilhada `feat/kg-v2-residuos-pr-b` (na prática a Wave 1 não usou branches separadas; a branch `-pr-c` chegou a ser criada num worktree mas ficou vazia e foi removida). **Code-only** (Wave 1):
o módulo de resolução + CLI + testes, **sem rodar sobre o corpus**. Sanity
`python -m radar.core.eval matching`: `recall@k 0.8334`, `noise 3.0`, exit 0 (baseline
inalterado — o passe não roda aqui). `ruff` limpo; `pytest tests/test_resolve_programas.py` 11/11.

**core/kg/resolve_programas.py** — três passos no padrão PROPOSE/APPLY:
1. **Inventory** (`inventory_programas`): varre os hipergrados e enumera todos os
   nós `Oportunidade(kind=programa)`, agregando por nome (112 names distintos, 129
   nós). Lixo óbvio ("programa" nu) sinalizado mas mantido (a higiene decide).
2. **Cluster + Resolve** (`cluster_programas` → `resolve_clusters`): agrupa por
   similaridade de embedding (cosseno ≥ 0.80) + adjudicação LLM nos clusters não-
   triviais, exatamente como `propose_merges` em canonicalize. Cada subcluster
   resolve contra o registro curado (`programas.json`): casa → `status: curado`;
   não casa → `status: promovido_auto` com id `programa:{slug}` (R2). A
   adjudicação e a carga da registry são os únicos pontos com LLM.
3. **Apply** (`apply`): reescreve os hipergrados — nós programa com alias viram
   aresta `pertence_a` apontando para o canônico; lixo óbvio é removido com suas
   arestas. O canon map `programa_canon` é persistido via kg_store.

**scripts/resolve_programas.py** — CLI com 6 subcomandos:
`stats`, `propose`, `sample -n`, `apply [--dry-run]`, `queue`, `report`.
Backup automático antes de `apply`. Idempotente (recompila canon de proposta
existente).

**tests/test_resolve_programas.py** — 11 testes:
- Inventário (encontra nós, agrega por nome, seleciona descrição mais longa)
- `corpus_programa_stats` (total/únicos)
- `build_canon` (compila canon map com curados + promovidos_auto)
- `apply` (resolve → aresta `pertence_a`, descarta lixo, passthrough sem nós,
  idempotência)
- `queue_unresolved` (filtra promovidos_auto)
- Purezas: `_UnionFind`, `_is_obvious_trash`
- `cluster_programas` NÃO está nos testes de CI (requer embeddings reais) — mesmo
  padrão de `propose_merges` em canonicalize.

**Não tocado:** o validador de lixo "programa" usa `anti_class_verdict` do PR-B
(categoriza "programa" como genérico); o descarte no apply aqui é redundante
(proteção mecânica). Os clusters com LLM (adjudicação) e a resolução contra a
registry são os únicos pontos não-testados em CI — exatamente o mesmo padrão de
`canonicalize.py`.

### PR-D — Granularidade atômica dos Conceitos · realizado 2026-07-05

Commit próprio na branch única empilhada `feat/kg-v2-residuos-pr-b`. **Code-only** (Wave 2):
prompts + split module + CLI + testes. `ruff` limpo; `pytest tests/test_split_concepts.py` 10/10.

**Frente 1 — Prompt atômico (R7).** Os 4 prompts de extração
(`_NODE_PROMPT`, `_CATALOG_NODE_PROMPT`, `_COMPANY_NODE_PROMPT`) ganharam a
diretiva: *"termo ATÔMICO — até 3 palavras, salvo nome próprio consagrado. 'X e
Y' vira DOIS Conceitos separados"* com o exemplo concreto do resíduo D
("agricultura de baixo carbono e uso eficiente de recursos" → dois conceitos).
Aplica-se a toda extração futura (edital, catálogo, perfil).

**Frente 2 — Split retroativo (`core/kg/split_concepts.py`).**
- `inventory_long_concepts(graphs, max_words=5)` — identifica os ~507 Conceitos
  com 5+ palavras (29% do corpus).
- `propose_splits(inventory)` — LLM decompõe cada name longo em termos atômicos
  (R7). Mantém como está se já é atômico ou nome próprio. Prompt com exemplos
  positivos/negativos.
- `apply_splits(graphs, plan)` — reescreve hipergrados: nó original removido, nós
  novos inseridos (herdam description + dim), arestas re-apontadas. Arestas com
  <2 membros pós-split são removidas. Dedup por id dentro de cada arquivo.
- `canonicalize_after_split(graphs)` — re-valida nós novos contra o canon map
  do PR-B (replay determinístico, sem LLM).

**scripts/split_concepts.py** — CLI: `stats`, `propose [--max-words] [--limit]`,
`apply [--dry-run]`, `report`.

**Testes (10/10):** word_count, inventário (filtra 5+, agrega cross-file),
apply (cria nós, reata arestas, cross-file, dedup, preserva intactos,
passthrough sem plano, aresta mantida pós-rename).

### Execução serializada dos passes de dados · realizada 2026-07-05 (supervisão Fable)

Sequência backup → B → sanity → C → sanity → D → sanity → E.1, conforme R5. Backups por
passe em `data/knowledge_graph.bak.*`. Sanity = `radar.core.eval matching` (baseline 0.8334/3.0).

**Passe B (higiene).** Validação: 61 descartes propostos; **3 falsos positivos revertidos
na supervisão** (aquicultura, CEIS, curtailment — conceitos de domínio reais). Aplicado:
96 instâncias descartadas (TRL ×3 grafias, LGPD, boilerplate legal/financeiro). Fusões:
o run inicial produziu só 3 grupos — **dois defeitos compostos** achados e corrigidos
(PR #60): `_variant_key` não tratava plural -ções/parentético/aglutinado, e o auto-merge
só rodava dentro dos clusters de embedding (cuja descrição separa as variantes triviais).
Pós-fix: **21 grupos (44→21 ids)**, cluster TIC unificado (4 grafias; a 5ª, com "(TIC)"
no nome, deslocava o embedding e ficou de fora por design — componentes conexos).
Sanity: 0.8334 / **2.875** (ruído melhorou de 3.0).

**Passe C (programas).** Mesmo padrão de defeito, mesmo fix (PR #61): `_program_key`
determinística (letra-dígito, prefixo genérico) no cluster e no índice do registro.
Antes: "centelha" promovido com colisão de id e família Rota 2030 em 3 canônicos;
depois: Centelha→curado, Rota unificada. Resultado: 111 menções → **94 canônicos**
(10 curados, 84 na fila `queue`), 18 menções viraram `pertence_a`, 3 lixos descartados.
Sanity estável.

**Passe D (granularidade).** 494 conceitos divididos → 1.112 termos atômicos, 516
arestas reatadas, replay do canon pós-split. Names 5+ palavras: **29% → 6%** (meta <10%).
Tecido conectivo: compartilhamento entre editais do MESMO macro-tema **0,79 → 1,00**
e entre temas diferentes 0,53 → 0,45 (mais conectivo E mais discriminativo). Amostra do
plano: ~1/15 split ruim ("H urbana" de hidrogênio truncado) — aceito, ruído marginal.
Sanity: 0.8334 / 3.125 (ruído subiu levemente com a atomização; dentro da faixa histórica).

**Passe E.1 (cobertura).** Macro-temas: aplicado — **24/32 editais (75%)**, 8/17
investimentos (os 9 sem macro são fundos GENERALISTAS: tese vazia é verdade do domínio,
não gap), 36/108 programas (menções nuas não têm conceitos próprios para mapear).
**Constraints: PREMISSA DA SPEC ERA FALSA** — o produtor funciona, mas o
`requisitos_texto` herdado da migração v1 é citação de lei ("Lei que altera a Lei
Federal n.º 10.973"), sem elegibilidade estruturável; o LLM corretamente não inventa.
A elegibilidade real está no TEXTO do edital e no `publico_alvo` do bronze — o caminho
é um produtor sobre essas fontes (escopo original de `feat/elig-constraints-producer`),
que precisa de decisão própria (fonte de input + golden). **Meta ≥95% de constraints é
INALCANÇÁVEL pela via da spec; recalibrada como pendência com dono.**

**Pendências saídas da execução:**
1. Produtor de constraints sobre texto do edital/`publico_alvo` (decisão de design + golden).
2. ~~4 arquivos-toco~~ RESOLVIDO PARCIAL (2026-07-05): `finep__1`/`fapesp__2` ("Edital 1"/
   "Edital 2", 1 nó, sem URL) eram toco puro e foram REMOVIDOS; `fapesp__18067` e
   `finep__743` são oportunidades REAIS com extração rala (zero Conceitos, invisíveis ao
   match) — pendência vira RE-EXTRAÇÃO; o 743 ainda tem prazo 2024 com status "aberto"
   (refresh do ETL).
3. Fila de 84 programas `promovido_auto` para o curador (`scripts/resolve_programas.py queue`).
4. Macro-temas de investimento poderiam vir direto de `tese_themes` do curado (hoje só
   via Conceitos); menções de programa poderiam herdar do edital de contexto — design call.
5. Deploy: republicar hipergrafos + canon maps no PG de prod (kg_store) — os passes rodaram
   no disco local.

### PR-E.2 — Regras de elegibilidade curadas · realizado 2026-07-05

**`data/curadoria/regras_elegibilidade.json`** — tabelas curadas (R4):
- `portes`: bandas FINEP (ME ≤ 4,8M, EPP ≤ 16M, média ≤ 90M, grande sem limite)
- `contrapartida`: tabela porte×região (percentuais mínimos compilados de
  instrumentos FINEP/FAPs)
- `sudam_sudene`: UFs beneficiadas e observações
- `interpretacoes`: receita (último exercício), grupo econômico, data de
  constituição

**`core/services/eligibility.py`** — novas funções:
- `load_curated_rules()` — cacheia o JSON em memória
- `porte_info(slug)` — label + faturamento_max + descricao de cada porte
- `contrapartida_minima(porte, uf)` — busca na tabela porte×região (resolve
  UF→região curta, N/NE/CO/SUDESTE/S)
- `format_receita_regra(profile)` — texto da interpretação + valor do perfil
- `_UF_REGIAO` — mapa UF→região curta adicionado ao módulo

**`domain/user_profile.py`** — campo `data_constituicao` adicionado (AAAA-MM-DD)
para a regra de 12 meses. Os demais campos (`faturamento_anual`, `tipo_entidade`,
`uf`, `ano_fundacao`) já existiam.

**Testes (20/20):** load_rules, porte_info (conhecido/desconhecido/todos),
contrapartida (por UF, genérica, porte desconhecido), format_receita, e os 11
testes originais de evaluate_constraint/evaluate_opportunity/is_eliminated
seguem passando.

**Não tocado:** PR-E.1 (cobertura — rodar extract_constraints no acervo inteiro)
é passe de dados, fica para a execução serializada.
