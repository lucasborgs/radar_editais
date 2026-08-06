# Descoberta de Oportunidades — vocabulário de busca (item 2.2)

Config autoritativa do agente de descoberta (`src/radar/core/ingestion/opportunity_discovery.py`).
Queries que varrem a web atrás de editais/chamadas/desafios de fomento espalhados
pelo Brasil. **Regra vive aqui (doc), não no `.py`** — ajuste sem deploy.

Princípio: queries amplas o bastante para recall, específicas o bastante para não
afogar em ruído. Resultados passam por triagem agêntica + classificação de
relevância v1 em shadow (`_row_with_relevance`), **apenas quando há material
classificável** (`texto_cru`/`descricao` não vazios), e entram no staging como
`provisorio` (§5.11). Registro sem material permanece `unclassified` pelo
default da migration 041. Erro/abstenção do classificador nunca fabrica
`out_of_scope` nem altera o fluxo editorial. O gate humano permanece obrigatório.
Ver `docs/historical/discovery-opportunities.md` e
`docs/specs/radar-data-trust-00-relevance-contract.md`.

**Divisão de trabalho com o feeder DOU (spec_dou_feeder.md §6.1):** com
`DISCOVERY_DOU_ENABLED=1`, o DOU é a *espinha de alta precisão* do fomento
federal publicado — as queries Tavily NÃO devem re-varrer essa zona (desperdício
+ overlap que o dedup por URL não pega). O Tavily mira o que o DOU não vê:
**FAPs/DOEs estaduais**, **desafios corporativos/open innovation** (anúncio
só-no-site) e **programas de aceleração/incubação** (Q4). Q3 (VC) fica FORA da
Descoberta: investidor é diretório curado, não cabe no schema de oportunidade.

**Unificação (Opção A, docs/domain/schema.md §12.4):** a Descoberta é a *torneira automática*
da fonte `web` — não tem bronze/índice próprios. Grava em `data/bronze/web_raw/`
(prefixo `web_discovery_`) no schema web (`url`/`url_hash`/`texto_cru`/
`verificacao=provisorio`), e daí entra como qualquer página web: chunkada pro RAG
pelo adapter `radar.pipeline.adapters.web` e indexada por `_build_editais("web")`. A
outra torneira do mesmo bronze é a seed list manual (`web_sources`).

```yaml
discovery:
  # Famílias de busca estáveis (RT03-T01). Identificam a finalidade de negócio
  # da query; métricas persistem somente o identificador, não a query completa.
  # Adicionar família exige registrar motivo no histórico, não criar novo canal.
  query_families:
    - key: state_innovation_funding
      description: Chamadas estaduais e FAPs fora das fontes dedicadas
    - key: corporate_open_innovation
      description: Desafios e pilotos publicados por empresas/hubs
    - key: startup_acceleration
      description: Aceleração, incubação e programas com benefício concreto
    - key: international_brazil_access
      description: Oportunidades internacionais acessíveis a empresas brasileiras

  # Queries de busca (Tavily), escopadas pras zonas NÃO-DOU (ver divisão acima).
  # Cada query declara `text` e `family`. A família deve estar registrada em
  # `query_families` acima. Tunáveis conforme a taxa de aprovação observada.
  queries:
    - text: "FAP estadual edital inovação chamada aberta"
      family: state_innovation_funding
    - text: "fundação de amparo à pesquisa edital empresas inovação inscrições abertas"
      family: state_innovation_funding
    - text: "edital fomento inovação empresas governo estadual aberto"
      family: state_innovation_funding
    - text: "desafio de inovação aberto empresas inscrições"
      family: corporate_open_innovation
    - text: "open innovation desafio tecnológico startups inscrições abertas"
      family: corporate_open_innovation
    - text: "programa de aceleração startups inscrições abertas edital"
      family: startup_acceleration
    - text: "incubadora seleção de startups chamada aberta"
      family: startup_acceleration
  # Caps por execução (controle de custo do crawl diário). Orçamentos SEPARADOS
  # por gerador: no 1º shadow-run o DOU rendeu ~63 candidatos/dia e, num cap
  # compartilhado, zerava o Tavily. max_candidates = busca cega (Tavily);
  # max_dou_candidates = teto defensivo do DOU (o pré-filtro já corta).
  max_results_per_query: 8
  max_candidates: 40
  max_dou_candidates: 80
  # Crawl de hub (1 nível, gated por DISCOVERY_HUB_CRAWL_ENABLED): quando a
  # triagem marca is_hub (portal de inovação aberta com vários desafios), até
  # este nº de desafios-filho por hub vira candidato. Custo = triagem+extração
  # por filho; o teto de hubs/execução é _MAX_HUBS_PER_RUN no código.
  max_hub_children: 8
  # Cache negativo: URLs rejeitadas na triagem são persistidas no ledger com
  # este TTL (dias). Dentro do TTL, a URL é pulada SEM nova chamada de triagem
  # (corta a re-triagem diária das mesmas URLs lixo). O TTL evita prender para
  # sempre uma URL cujo conteúdo pode virar relevante; calibrar pela cadência de
  # mudança das fontes (fontes estáveis toleram TTL maior).
  reject_cache_ttl_days: 30

  # Canal Deep Research (spec discovery-deep-research.md §10 do strategy) —
  # CANAL COMPLEMENTAR aos scrapers determinísticos, gated por
  # DISCOVERY_DEEP_RESEARCH_ENABLED=1 (padrão dos outros geradores; `enabled`
  # aqui é documental e descreve a postura default: off). Usa o engine real
  # `radar.core.deep_research.run_deep_research` (subagente web_search +
  # fetch_url, sempre COM citação). MIRA o que DOU/scrapers não veem: linhas de
  # crédito e produtos de inovação sem edital, FAPs/DOEs pouco estruturados,
  # desafios corporativos, aceleradoras/incubadoras, ICTs/laboratórios (PNIPE)
  # e novas fontes de fomento. NUNCA publica no catálogo/KG: cada fonte citada
  # vira 1 candidato no staging `discovered_opportunities` (pending,
  # discovery_channel='deep_research') e exige gate humano. O pacote de
  # evidências preserva URL, citações, data, fonte, campos ausentes, conflitos
  # e confiança (spec §2). Falha do Deep Research degrada o canal (skip) sem
  # interromper a descoberta determinística (aceite 5).
  deep_research:
    enabled: false
    # Backend do subagente (resolve em radar.core.deep_research.run_deep_research).
    # Env DISCOVERY_DEEP_RESEARCH_PROVIDER sobrescreve; default: anthropic.
    provider: anthropic
    # Teto de candidatos por execução (controle de custo do agente).
    max_findings: 10
    # Alvos de pesquisa do piloto. Cada alvo = uma pergunta/brief; `type_hint`
    # sinaliza o opportunity_type esperado para o revisor (edital|desafio|
    # programa|ict). Adicionar alvo exige registrar motivo no histórico, como
    # as famílias de busca.
    targets:
      - key: credit_lines
        brief: "Linhas de crédito e produtos de inovação para empresas de base tecnológica no Brasil abertos atualmente (BNDES, bancos públicos e agentes financeiros): condições, elegibilidade, página oficial e processo de acesso."
        type_hint: edital
      - key: corporate_challenges
        brief: "Desafios corporativos e open innovation abertos no Brasil para startups deep-tech: problema proposto, empresa promotora, formato de participação, inscrição e prazo."
        type_hint: desafio
      - key: accelerators_incubators
        brief: "Programas de aceleração e incubação abertos no Brasil para startups de base tecnológica: estágio aceito, apoio, duração, contrapartida, inscrição e prazo."
        type_hint: programa
      - key: ict_labs
        brief: "ICTs e laboratórios brasileiros (incluindo o PNIPE) com infraestrutura acessível a empresas: competência, equipamento, localização e condições de acesso."
        type_hint: ict
      - key: new_sources
        brief: "Novas fontes de fomento à inovação no Brasil ainda não cobertas por FINEP/FAPESP/FAPESC/DOU (fundações, associações, programas regionais): portais, chamadas abertas, condições e inscrição."
        type_hint: edital
```
