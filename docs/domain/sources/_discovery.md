# Descoberta de Oportunidades — vocabulário de busca (item 2.2)

Config autoritativa do agente de descoberta (`src/radar/core/ingestion/opportunity_discovery.py`).
Queries que varrem a web atrás de editais/chamadas/desafios de fomento espalhados
pelo Brasil. **Regra vive aqui (doc), não no `.py`** — ajuste sem deploy.

Princípio: queries amplas o bastante para recall, específicas o bastante para não
afogar em ruído. Resultados passam por triagem agêntica + entram no KG como
`provisorio` (§5.11). Ver `docs/historical/discovery-opportunities.md`.

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
  # Queries de busca (Tavily), escopadas pras zonas NÃO-DOU (ver divisão acima).
  # Tunáveis conforme a taxa de aprovação observada.
  queries:
    # FAPs / fomento estadual (DOEs não entram no feeder DOU federal)
    - "FAP estadual edital inovação chamada aberta"
    - "fundação de amparo à pesquisa edital empresas inovação inscrições abertas"
    - "edital fomento inovação empresas governo estadual aberto"
    # Desafios corporativos / open innovation (anúncio só-no-site)
    - "desafio de inovação aberto empresas inscrições"
    - "open innovation desafio tecnológico startups inscrições abertas"
    # Programas de aceleração / incubação (Q4)
    - "programa de aceleração startups inscrições abertas edital"
    - "incubadora seleção de startups chamada aberta"
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
```
