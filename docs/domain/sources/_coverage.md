# Cobertura da Descoberta — canais de aquisição de oportunidades

Registry autoritativo dos canais de aquisição da Descoberta (Radar Data Trust 03).
Código lê via `radar.core.kg.schema.coverage_channels()`. **Regra vive aqui (doc),
não no `.py`** — adicionar ou remover canal sem deploy.

Apenas canais de aquisição de **oportunidades** entram aqui. Catálogos de atores
(investidores, ICTs, programas) têm seus próprios registros e não são canais desta
spec.

`open_search` é o **canal lógico** de busca aberta. Tavily é o backend atual de
`radar.core.web_search`, não uma decisão permanente do domínio. Trocar ou adicionar
provider no futuro não muda staging, triagem ou métricas públicas.

## Canais de aquisição

```yaml
coverage:
  channels:
    - source_key: finep
      display_name: FINEP
      mode: dedicated
      scope_note: Coleta determinística do portal FINEP
      expected_interval_hours: 24
      enabled_by_default: true

    - source_key: fapesp
      display_name: FAPESP
      mode: dedicated
      scope_note: Coleta determinística do portal FAPESP
      expected_interval_hours: 24
      enabled_by_default: true

    - source_key: fapesc
      display_name: FAPESC
      mode: dedicated
      scope_note: Coleta determinística do portal FAPESC
      expected_interval_hours: 24
      enabled_by_default: true

    - source_key: web_curated
      display_name: Web curada
      mode: curated_web
      scope_note: URLs aprovadas em web_sources
      expected_interval_hours: 24
      enabled_by_default: true

    - source_key: open_search
      display_name: Busca aberta
      mode: open_search
      scope_note: Oportunidades não cobertas pelas fontes dedicadas e pelo DOU; canal lógico sobre o motor de busca existente
      expected_interval_hours: 24
      enabled_by_default: true

    - source_key: dou
      display_name: Diário Oficial da União
      mode: official_feed
      scope_note: Oportunidades publicadas no DOU
      expected_interval_hours: 24
      enabled_by_default: false
      flag_name: DISCOVERY_DOU_ENABLED

    - source_key: hub_expansion
      display_name: Expansão de hubs
      mode: hub
      scope_note: Desafios-filho encontrados em hubs de inovação aberta
      expected_interval_hours: 24
      enabled_by_default: false
      flag_name: DISCOVERY_HUB_CRAWL_ENABLED
```

## Modos canônicos

| Modo | Finalidade |
|---|---|
| `dedicated` | Scraper dedicado por fonte (FINEP, FAPESP, FAPESC) |
| `curated_web` | URLs de web curadas aprovadas em web_sources |
| `open_search` | Busca ampla por motor de busca (canal lógico) |
| `official_feed` | Feed oficial governamental (DOU) |
| `hub` | Expansão de desafios-filho de hubs de inovação |

## Invariantes

- `source_key` é lowercase, sem underscores duplos, único entre canais.
- `mode` pertence ao conjunto canônico acima.
- Canal gated por flag registra o nome da flag (`flag_name`), nunca seu valor ou segredo.
- `open_search` não nomeia Tavily como canal normativo.
