# Spec — Radar como superfície explícita

**Status:** vigente · **Data:** 2026-07-14
**Escopo da primeira entrega:** Radar explícito, explicável e acionável.
**Fora do escopo desta entrega:** alterar ranking/embeddings/elegibilidade, comparação, timeline de prazo, estados operacionais da descoberta e canvas avançado de projeto.

**Documento-pai:** [system-coherence.md](system-coherence.md).

---

## 1. Resultado de produto

Transformar o Radar de um resultado eventual do chat em uma superfície própria
do produto: uma empresa com perfil mínimo pode ver oportunidades priorizadas,
entender por que cada uma apareceu, identificar o que falta para confirmar a
elegibilidade e iniciar a próxima ação.

O princípio é: **afinidade explicada, não promessa de aprovação**. O ranking
continua sendo do motor v3; a UI não converte similaridade em probabilidade nem
faz recomendação absoluta.

Jornada alvo:

```text
Explorar ou preenchimento de perfil
  → perfil mínimo (nome + atividades)
  → Radar explícito
  → entender evidência e elegibilidade
  → abrir ficha ou iniciar proposta/pitch
```

## 2. Decisões travadas

| # | Decisão |
|---|---|
| D1 | O Radar ganha rota própria `/radar`; o chat continua sendo superfície de descoberta e não é removido. |
| D2 | O resultado é calculado por endpoint explícito, sem depender de o ExploreAgent decidir chamar uma tool. |
| D3 | A ordenação continua por `affinity` do match v3. `score` é evidência do melhor par de trechos e não recebe nome de percentual/probabilidade. |
| D4 | Sem login, o Radar usa o perfil local e chunks efêmeros; com login, usa o workspace e `company_chunks` (incluindo biblioteca), além de habilitar vereditos cacheados. |
| D5 | Editais, programas e investidores são exibidos em trilhas distintas; não se reordena uma trilha usando veredito LLM. |
| D6 | O contrato canônico de cards é o payload atual de `OpportunityMatch.to_dict()` e `InvestorMatch.to_dict()`. Não haverá mudança de schema de banco nesta fase. |
| D7 | Elegibilidade `inelegivel` continua eliminada no radar.api. A UI mostra apenas `elegivel` e `nao_verificada`; esta última deve ser acionável. |
| D8 | O painel não altera a descoberta, o ingest gold ou o RAG. Uma oportunidade promovida aparece automaticamente quando o pipeline existente a tiver inserido no catálogo gold. |

## 3. Estado atual e lacuna

Hoje, o `/explore` só acrescenta cards estruturados quando o agente chama uma
das tools de match. Isso torna o Radar dependente de decisão probabilística do
agente e mistura duas intenções distintas: explorar o ecossistema e ver o
ranking pessoal.

O frontend já possui os componentes e tipos fundamentais:

- `MatchedEditalCard`, `MatchedEntityCard` e `VerdictBlock`;
- `matched_excerpts[]`, setores, prazo, valor e elegibilidade;
- contratos de cards e snapshots de match compatíveis com o payload do backend;
- perfil em `localStorage` e gate `isRadarReady()`.

Portanto, a primeira entrega cria composição, rota e contrato explícito; não
reescreve o motor de match.

## 4. Contrato da API

Novo endpoint: `POST /radar/matches`.

### Request

```json
{
  "profile": { "nome": "", "descricao_atividades": "" }
}
```

- `profile` usa `CompanyProfileSchema` já compartilhado.
- Perfil mínimo: `nome` e `descricao_atividades`; ausência retorna `422` com
  `{ "error": "profile_incomplete", "missing_fields": [...] }`.
- Autenticação é opcional, com os mesmos limites de `/explore`: anônimo 3/min,
  autenticado 10/min.

### Response

```json
{
  "matched_editais": [],
  "matched_programas": [],
  "matched_investidores": [],
  "meta": {
    "ranking": "affinity",
    "as_of": "2026-07-13",
    "uses_workspace_chunks": false
  }
}
```

- Editais e programas vêm de `find_matching_opportunities`, filtrados por kind.
- Investidores vêm de `find_matching_investors`.
- Usuário autenticado resolve `workspace_id` e passa `workspace_id`/`db` ao
  motor. Usuário anônimo usa chunks efêmeros. Este detalhe é essencial para não
  prometer que a biblioteca influencia um Radar anônimo.
- Para autenticados, o endpoint anexa vereditos cacheados e enfileira misses
  com o mecanismo atual. Para anônimos não há LLM de veredito nem persistência.
- O endpoint não chama ExploreAgent e não altera a ordenação por veredito.

## 5. Experiência `/radar`

### Estados

1. **Sem perfil mínimo:** explica o que é preciso e leva a Explorar/Perfil;
   não dispara embedding nem match.
2. **Carregando:** skeleton por trilha, com mensagem curta sobre análise de
   afinidade e elegibilidade.
3. **Com resultados:** trilhas Editais, Programas e Capital privado.
4. **Sem resultados:** mensagem honesta, com ações para detalhar atividades ou
   adicionar material à biblioteca (quando autenticado).
5. **Falha:** erro recuperável com botão de tentar novamente; nunca usa cards
   antigos como se fossem resultado atual.

### Card de edital

- Cabeçalho: nome, fonte, prazo, ticket e setores.
- Indicador nomeado **Afinidade de escopo**, acompanhado de tooltip: ranking é
  por afinidade média entre trechos, não chance de aprovação.
- Bloco expansível **Por que apareceu** com `matched_excerpts[]`.
- Bloco de elegibilidade: aprovado quando conhecido; quando não verificado,
  lista campos faltantes e CTA para completar perfil.
- Veredito LLM é secundário, assíncrono e apresentado como análise de riscos,
  nunca como substituto de elegibilidade ou ranking.
- Ações: abrir ficha oficial e iniciar proposta.

Programas reutilizam o padrão de oportunidade; investidores usam tese, ticket,
estágio e CTA de pitch. A fase não implementa comparação entre cards.

## 6. Dependências e ordem de implementação

```text
F0: contrato/API determinística
 ├── F1: tipos + cliente HTTP
 │    └── F2: página /radar e estados de UX
 │         └── F3: cards e vereditos na nova composição
 └── F4: testes de integração e jornada
```

| Fase | Depende de | Entrega | Não pode quebrar |
|---|---|---|---|
| F0 | `match_v3`, auth opcional, `company_chunks` | router/endpoint e contrato | ranking, Stage 0–2, RLS, cache de veredito |
| F1 | F0 | tipos TS e `radarMatches()` em `api.ts` | contrato do chat `/explore` |
| F2 | F1, perfil local | `/radar`, gates e estados | front door `/` e persistência do perfil |
| F3 | F2 | copy/evidência/eligibilidade, polling de vereditos | CTA de writing/pitch e cards existentes no chat |
| F4 | F0–F3 | testes e QA | limites de custo e comportamento anônimo |

### Dependências críticas

- `match_v3` lê Postgres e pode gerar `company_chunks`; a rota deve respeitar
  RLS e nunca aceitar `workspace_id` do cliente.
- `company_chunks` é enriquecido pela biblioteca somente no caminho autenticado.
- `match_verdicts` é por workspace; o endpoint anônimo não pode ler, escrever
  ou enfileirar esse dado.
- `deadline` e `status` são resolvidos pelo Stage 0; a UI apenas apresenta o
  resultado e não replica essa regra no TypeScript.
- A descoberta entra no Radar somente depois do ingest gold. A UI não consulta
  `discovered_opportunities`, que é uma fila administrativa global.
- A escrita continua usando `edital_chunks`; o Radar consome `match_chunks`.
  Não misturar essas duas superfícies de embedding.

## 7. Fora de escopo e próximas specs

- **Radar Fase 2:** timeline/urgência de prazo, filtros e comparador de
  oportunidades. Depende do contrato F0, mas não de mudança no radar.pipeline.
- **Discovery operations:** estados `silver_ready`, `catalog_ready`,
  `radar_ready`, `rag_ready`, retry e histórico. Depende de migration e tasks;
  deve ter spec própria.
- **Canvas avançado de projetos:** problema, solução, TRL,
  parceiros e mecanismo; transição explícita para o Radar. Depende da decisão
  de produto sobre armazenamento e memória, portanto não entra nesta branch.

## 8. Critérios de aceite

1. Uma empresa com perfil mínimo acessa `/radar` e recebe resultados sem mandar
   uma mensagem ao ExploreAgent.
2. A mesma consulta autenticada usa o workspace; a anônima não lê dados de
   outro workspace nem gera `match_verdicts`.
3. Os resultados preservam a ordem por `affinity` dentro de cada trilha.
4. Todo edital exibido mostra fonte, prazo/ausência de prazo, setores e ao
   menos uma evidência quando `matched_excerpts` existir.
5. Nenhuma copy chama score de probabilidade, aprovação ou garantia.
6. `nao_verificada` mostra os critérios pendentes; `inelegivel` não aparece.
7. Vereditos chegam via cache/poll sem bloquear a primeira renderização.
8. Fluxos existentes de chat, escrita, pitch, catálogo e descoberta continuam
   passando nos testes relevantes.

## 9. Verificação

- Testes unitários do router: perfil incompleto, anônimo, autenticado, divisão
  por kind, workspace forwarding e ausência de veredito anônimo.
- Testes do cliente/frontend para os cinco estados da página.
- `pytest` focal nos serviços/routers alterados; `ruff check` nos arquivos
  modificados; `cd frontend && npx tsc --noEmit`.
- QA manual: perfil mínimo anônimo, perfil autenticado com biblioteca,
  elegibilidade não verificada, CTA de proposta, CTA de pitch e polling de
  veredito.
- Não rodar eval de matching nesta fase, salvo se for alterado
  `src/radar/core/services/match_v3.py`, `eligibility.py`, embeddings ou o corpus.
