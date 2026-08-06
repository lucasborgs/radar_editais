# PNIPE — Schema específico

Estende [docs/domain/schema.md](../schema.md). Só documenta o que diverge do schema global.

---

## 1. Identidade

```yaml
source:
  key: pnipe
  display_name: PNIPE
  graph_tag: pnipe
```

PNIPE (Plataforma Nacional de Infraestrutura de Pesquisa, MCTI) cataloga
**laboratórios e infraestrutura de pesquisa** do país. Não é fonte de editais:
não lança chamadas, não é financiador. Os laboratórios são materializados como
**capacidades** (`entities kind=ict`, fonte `pnipe`), seguindo a
[spec ict-pnipe-capabilities.md](../../specs/ict-pnipe-capabilities.md) e o
§6.1.2 do [schema global](../schema.md).

---

## 2. Postura de integração

```yaml
integration:
  mode: curated_entry_point        # SPA client-side sem API pública estável
  live_fetch: false
  normalized_by: radar.pipeline.extractors.pnipe.PnipeScraper
  input_artifact: "data/bronze/ict_raw/pnipe_dump.json"
  output_artifact: "data/bronze/ict_raw/pnipe_*.json"
  registry: none                    # NÃO entra no SCRAPER_REGISTRY (não é edital)
```

Sondagem em 2026-08-04: `https://pnipe.mcti.gov.br/search` é uma SPA React;
`/api/search`, `/api/labs` e `/api/v1/labs` respondem 200 com o HTML shell
(sem JSON) e não há API pública estável documentada. Logo, o "scraper" é um
**ponto de entrada offline**: o operador grava um dump curado em
`pnipe_dump.json` e roda `python -m radar.pipeline.extractors.pnipe`, que
normaliza cada registro (`parse_pnipe_record`) para o bronze versionado. A
ausência de dump → nenhum lab materializado (a Descoberta web/gate humano pode
alimentar esse dump no futuro, sem mudança de contrato).

---

## 3. Contrato do registro bronze

Consumido por `gold._ingest_icts` (fonte `pnipe`) → `entities(kind=ict)`.

```yaml
pnipe_schema:
  artifact: "data/bronze/ict_raw/pnipe_*.json"
  id_format: "pnipe:<slug>"          # ex.: pnipe:lab-ia-robotica
  node_fields: [id, name, kind, source, url, about, institution, institution_type, address, municipio, competencias, equipamentos, condicoes_acesso, contact, areas_raw, data_extracao]
  required_fields: [name, url, data_extracao]
  kinds: [laboratorio]
  sources: [pnipe]
  notes:
    - "Registro = UM laboratório. kind='laboratorio' distingue de unidade EMBRAPII; todos materializam entities kind=ict (capacidade), variante preservada em metadata.pnipe_kind."
    - "data_extracao é a DATA DE VERIFICAÇÃO do curador. Vira collected_at do SourceBundle, entities.verificado_em e o path de proveniência metadata.verificado_em."
    - "competencias/equipamentos/condicoes_acesso/institution/municipio: declarados pela fonte, passam VERBATIM a metadata.capacidades e entram no description/embedding. Não inferimos disponibilidade atual nem parceria."
    - "areas_raw: temas DECLARADOS pela fonte → normalização gold de setores/tags (§13). Ausente → [] (match por competência segue via semântica do description)."
    - "contact: dict {responsavel, email, telefone, site} — campos opcionais, copiados sem contato automático."
    - "Sem aresta credenciada_por (não há credenciamento EMBRAPII). Sem completude nacional: o índice é uma amostra curada."
```

### 3.1 Mapeamento dump raw → bronze

`parse_pnipe_record` (src/radar/pipeline/extractors/pnipe.py) normaliza o dump
curado (`pnipe_dump.json`) para o contrato acima:

```yaml
bronze_mapping:
  nome | name:       name
  slug:              slug            # derivado via schema.slugify quando ausente
  url:               url             # página oficial do lab no índice PNIPE
  descricao | about: about
  instituicao:       institution
  tipo_instituicao:  institution_type
  endereco + municipio + uf: address
  municipio:         municipio
  competencias:      competencias[]  # lista
  equipamentos:      equipamentos[]  # lista
  condicoes_acesso:  condicoes_acesso
  contato_responsavel / contato_email / contato_telefone / contato_site: contact{...}
  areas:             areas_raw[]     # temas declarados; ausente → []
  verificado_em | data_extracao: data_extracao
```

Erro em registro (sem `name`/`url`/`data_extracao`) → `ValueError`; o `run()`
da base degrada para `[]` e o erro é logado (nunca grava bronze parcial sem
controle).

---

## 4. Proveniência e data de verificação

`gold._ingest_icts` ancora CADA laboratório num único `EvidenceRef`
`document_only` (reusa `provenance_writer.build_ict_record_anchor`): `document`
= nome do arquivo `pnipe_*.json`, `canonical_content_hash` = md5 do JSON do
registro individual, `source_url` = URL oficial do laboratório. Tabela de fatos:

| Path | state | producer.kind |
|---|---|---|
| `name`, `metadata.url` | stated | adapter (`pnipe_lab_index`) |
| `metadata.institution` | stated | adapter |
| `metadata.municipio` | stated | adapter |
| `metadata.competencias` | stated | adapter |
| `metadata.equipamentos` | stated | adapter |
| `metadata.condicoes_acesso` | stated | adapter |
| `metadata.verificado_em` | stated | adapter |
| `uf` (só quando derivada) | inferred | deterministic |
| `setores`/`tecnologias_tags` | inferred | deterministic |

Campo sem valor no registro não recebe entrada — nada de `unknown` artificial.
Sem chunks/RAG para ICTs (spec §3.4).

---

## 5. Não-claims (spec §4)

- Não representa completude nacional do PNIPE — é uma amostra curada;
  ausência de um lab no índice NUNCA é apresentada como ausência no Brasil.
- Não inferimos disponibilidade atual do equipamento nem parceria: apenas o
  que a fonte declara (o match fala em "capacidade candidata", a validar com a
  ICT).
- Não há contato automático com a ICT; `contact` é display.
- Laboratório NUNCA é tratado como edital/oportunidade de financiamento.

---

## 6. Gotchas

- O arquivo bronze DEVE ser versionado (`pnipe_*.json`): `_ingest_icts` lê o
  último arquivo em ordem alfabética (`files[-1]`), mesmo padrão EMBRAPII; o
  `data_extracao` de cada registro identifica quando foi verificado.
- `provenance_backfill._load_ict_catalog` também lê `pnipe_*.json` — o backfill
  de proveniência cobre os dois tipos de ator ICT.
- Não adicionar ao `SCRAPER_REGISTRY`: aquele registry percorre o ETL de
  edital (PDF/status/mechanism/vigência), que não se aplica a capacidades.
