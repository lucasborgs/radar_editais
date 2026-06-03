# Backlog — pendências para posterioridade

> Documento **vivo**. Itens conscientemente adiados (não esquecidos). Cada item
> traz contexto suficiente para retomar sem reconstruir o raciocínio: **o quê**,
> **por que adiado**, **onde está specado**, **ponto de entrada**, **status**.
>
> Convenção: ao concluir um item, mova-o para "Concluídos" (com o commit/PR) ou
> remova-o. Ao adiar algo novo, adicione aqui na hora — o custo de esquecer é alto.

---

## Aberto

### ICT — Fase C.2: ICT na escrita (peça 4)
- **O quê:** quando o usuário escolhe um parceiro ICT na tela do grafo, importá-lo
  para a ContentLibrary do workspace (`create_item(type_='ict_partner', …)`) para
  que `search_library` o use ao escrever a seção de parceria — com proveniência.
- **Por que adiado:** Fase C.1 (matchmaking) entrega o valor central; a escrita é
  extensão. Depende de UI (botão "selecionar parceiro" na tela do grafo).
- **Onde:** [spec_ict_phase_c.md](spec_ict_phase_c.md) peça 4 / fase C.2.
- **Ponto de entrada:** endpoint `POST /library/from-ict` + reuso de
  `create_item`/`enrich_content_task`/`search_library` (tudo já existe).
- **Guard-rail (não violar):** o Redator **não** recebe `find_ict_partners` nem lê
  `icts.json`. ICT entra na escrita só via decisão humana → library. Sugestão ≠
  compromisso. Teste de aceitação por grep.
- **Status:** aberto.

### ICT — Fase B: fonte PNIPE/MCTI
- **O quê:** segunda fonte de ICTs — laboratórios do [PNIPE](https://pnipe.mcti.gov.br/search)
  (metadados ricos: Sobre, Endereço, Contato, área de atuação, técnicas).
- **Por que adiado:** PNIPE é grande e ruidoso (toda a infraestrutura de C&T do
  país) → exige estratégia de filtro/paginação antes de entrar no grafo. EMBRAPII
  (Fase A) já provou o tipo de nó end-to-end.
- **Onde:** [spec_ict_mapping.md](spec_ict_mapping.md) Fase B.
- **Ponto de entrada:** `pipeline/extractors/ict_pnipe.py` (espelha
  `ict_embrapii.py`); dedup cross-source já existe em `build_ict_graph` (por nome
  normalizado). Verificar se a busca é client-side (pode exigir Playwright).
- **Status:** aberto.

### ICT — tuning do flag `requires_ict_partner`
- **O quê:** refinar os patterns regex §5.10 conforme o corpus cresce. Recorrente,
  não tarefa única.
- **Por que adiado:** heurística atual (10/20 marcados) é aceitável para MVP; tuning
  pede mais editais e ground-truth.
- **Onde:** WIKI.md §5.10 (`ict_requirement_patterns`) — patterns no doc, sem deploy.
- **Ponto de entrada:** (1) rotular amostra (exige ICT? s/n) → medir precisão/recall;
  (2) falso-positivo conhecido: FAQ "a ICT pode ser coexecutora? Não"; (3) considerar
  patterns negativos; (4) se empacar, graduar para classificador LLM no build.
- **Status:** aberto (recorrente).

### DeepResearch — implementação
- **O quê:** busca web com fonte, como subagente-como-tool; grounding → working
  memory → gate do usuário → learning na ContentLibrary (nunca no KG).
- **Por que adiado:** só design por ora; priorizado o mapeamento de ICTs.
- **Onde:** [spec_deepresearch.md](spec_deepresearch.md).
- **Ponto de entrada:** `core/web_search.py` (port Tavily) + `core/agent_tools/research_tools.py`.
  Backend escolhido: **Tavily**. Reusa `fetch_page` e `enrich_content_task`.
- **Status:** aberto.

---

## Débitos conhecidos (menores)

- **`domain/vocabulary.canonicalize_themes` é stub** (só lowercase/dedupe). O vocab
  canônico de temas vive em WIKI.md §5.9; quando uma fonte emitir variação de tema,
  implementar o mapa de sinônimos para convergir ao §5.9.
- **Export Obsidian ainda FINEP-only** — nós `ict` não são exportados ao vault.
- **Flag só sobre texto coletado** — exigência de ICT em anexo PDF não baixado é
  falso-negativo estrutural (limite da heurística, documentado em §5.10).

---

## Concluídos (referência)

- **ICT Fase A** (ingestão EMBRAPII + schema + icts.json) — commit `381810614`.
- **ICT Fase C.1** (flag + query + tool no Explorador) — commit `381810614`.
