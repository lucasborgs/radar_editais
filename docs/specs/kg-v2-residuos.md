# Spec — KG v2: resíduos de qualidade + ranking unificado

Status: **aprovada** · 2026-07-05 · escopo: segunda demão de qualidade pós-redesign (higiene, resolução de programas, granularidade, cobertura) + ranking decrescente unificado no radar + regras de elegibilidade como dado curado

> Continuação de [`kg-redesign.md`](kg-redesign.md) (8 PRs implementados). Esta spec trata o que o
> diagnóstico pós-implementação (2026-07-05) mediu como resíduo — nada aqui é falha de arquitetura;
> é profundidade/cobertura dos passes de qualidade e um bug de UX no radar.
>
> Pré-beta, sem usuários reais: **sem gates estatísticos nem testes exaustivos**. A suíte
> `python -m core.eval matching` roda como sanity após cada passe de dados. O porquê de cada
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

Branch `feat/kg-v2-residuos-pr-a` (2 commits). Sanity `python -m core.eval matching`:
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
