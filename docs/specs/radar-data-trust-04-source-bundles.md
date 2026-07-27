# Radar Data Trust 04 — Pacotes documentais versionados

**Status:** aprovada para implementação · **Data:** 2026-07-27  
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)  
**Contratos anteriores:** [`radar-data-trust-01-provenance.md`](radar-data-trust-01-provenance.md), [`radar-data-trust-02-quality-gates.md`](radar-data-trust-02-quality-gates.md)  
**Ordem:** 04 · **Impacto:** alto e incremental; aquisição, vigência e confiança documental

---

## 1. Problema comprovado

O Radar já persiste o Documento Canônico de cada edital em
`edital_source_docs`, mas mantém apenas a projeção corrente por `edital_id`.
Isso protege o runtime contra disco efêmero, porém não responde com segurança:

- quais documentos compunham a oportunidade em uma coleta anterior;
- quando edital, anexo, FAQ ou retificação entrou no conjunto;
- qual documento alterou uma regra anterior;
- por que determinado valor deixou de ser vigente;
- se duas fontes autorizadas permanecem em conflito; e
- qual versão sustentou uma entidade, relação, match chunk ou chunk de escrita.

A FAPESC já coleta edital-base e retificações em `documentos_normativos`, e os
adapters já aceitam `metadata`. A proveniência da spec 01 já transporta hashes,
coordenadas e estados factuais. A lacuna é menor que um novo pipeline: falta um
histórico durável e uma regra conservadora de composição entre documentos.

Para atores, os JSONs versionados e alguns snapshots oficiais existentes
permitem rastrear parte da origem, mas não formam um pacote recuperável e
atualizável. ICTs e investidores continuam com conteúdo insuficiente em vários
casos; a spec deve tornar essa insuficiência explícita, não preenchê-la.

Descobertas Web também podem ser compostas por mais de uma página. Um portal
corporativo de inovação aberta pode concentrar regras gerais e FAQ, enquanto
cada desafio possui uma página própria com problema, benefício e caminho de
participação. Guardar somente a página encontrada primeiro perde parte do
contrato da oportunidade.

## 2. Resultado

Entregar um pacote documental versionado para oportunidades e atores que:

1. preserve cada versão material adquirida;
2. identifique o papel de cada documento;
3. componha edital-base, anexos e retificações sem apagar o histórico;
4. resolva precedência somente quando houver evidência documental suficiente;
5. produza a projeção corrente consumida pelo pipeline existente;
6. ligue a proveniência à versão efetivamente usada; e
7. exponha ausência ou conflito sem fabricar certeza.

Em termos de produto, cada afirmação relevante poderá ser ligada ao dossiê
documental que estava vigente quando foi produzida.

## 3. Princípios de simplicidade

1. **Evoluir o seam existente:** `SourceAdapter`, Documento Canônico,
   `source_docs`, silver e gold permanecem o único data plane.
2. **Uma persistência nova:** uma tabela append-only de versões basta; não
   criar banco documental, event sourcing genérico ou tabela por tipo de fonte.
3. **JSONB na fronteira existente:** documentos continuam no formato canônico;
   não normalizar páginas, blocos e anexos em tabelas próprias sem necessidade
   comprovada.
4. **Precedência determinística e conservadora:** metadado explícito vence;
   ambiguidade vira `conflicting`, não heurística ou chamada LLM.
5. **Sem crawler universal:** cada produtor atual continua responsável por
   obter seu conteúdo. Novas aquisições usam URLs já conhecidas pelo scraper,
   staging ou catálogo.
6. **Sem framework novo:** usar biblioteca padrão e dependências já adotadas
   para hash, datas, HTTP e parsing. Dependência nova exige lacuna comprovada.
7. **Sem backfill total no pré-beta:** validar uma amostra representativa e
   popular versões novas pelo fluxo normal.
8. **Sem dupla verdade:** `edital_source_docs` permanece projeção de
   compatibilidade; o histórico versionado torna-se autoridade das versões.

## 4. Escopo

### 4.1 Oportunidades

O pacote de uma oportunidade pode conter:

| Papel | Uso |
|---|---|
| `base_notice` | edital, chamada ou regulamento principal |
| `opportunity_page` | página principal de desafio ou oportunidade sem edital formal |
| `program_page` | regras gerais compartilhadas por oportunidades de um programa ou portal |
| `annex` | anexo normativo ou operacional |
| `amendment` | retificação, errata ou rerratificação |
| `official_page` | página oficial complementar, como landing page ou estado |
| `faq` | esclarecimento oficial, quando efetivamente normativo |

FINEP, FAPESP, FAPESC e Web/Descoberta continuam usando seus adapters e IDs
atuais. As fixtures iniciais devem conter tanto uma oportunidade Web composta
por portal + desafio quanto uma retificação FAPESC. Isso impede que o contrato
seja otimizado apenas para PDFs e fontes públicas dedicadas.

Para um portal corporativo:

- a página específica do desafio é `opportunity_page`;
- a página geral do portal é `program_page` e fica `contextual`;
- FAQ geral permanece associada ao programa e pode complementar a
  oportunidade;
- conteúdo específico do desafio prevalece sobre conteúdo geral quando ambos
  tratam do mesmo campo; e
- a oportunidade pode continuar ligada a um `programa` estável pelo
  `subordinado_a` existente.

A mesma `program_page` pode integrar, como contexto, os bundles de diferentes
desafios. O `content_hash` comum prova que se trata do mesmo snapshot; no
pré-beta não é necessário criar uma tabela adicional apenas para deduplicar
fisicamente esse texto.

A empresa operadora não deve ser convertida em `agencia`. Enquanto não houver
uso comprovado para um novo tipo de ator corporativo, seu nome permanece como
operador do programa e origem oficial dos documentos.

### 4.2 Atores

O pacote de um investidor, ICT, programa ou agência pode conter:

| Papel | Uso |
|---|---|
| `official_page` | página institucional oficial recuperada |
| `official_record` | registro oficial estruturado, como EMBRAPII |
| `curated_record` | registro versionado do repositório |

O pacote não transforma ator em oportunidade e não cria RAG artificial.
Ausência de conteúdo oficial suficiente permanece um gap visível.

### 4.3 Enriquecimento incremental de atores

Esta spec não exige que um ator possua uma “página completa”. Ela permite
acumular evidência oficial fragmentada ao longo do tempo:

- ICT: registro EMBRAPII, página institucional, competências, laboratórios e
  projetos/casos publicados;
- investidor: site, tese declarada, portfólio e notícias de investimentos;
- programa: página institucional, FAQ, edições e resultados publicados; e
- agência: mandato, instrumentos e páginas oficiais relevantes.

Cada novo documento oficial cria uma versão do pacote; os fatos por campo são
recalculados somente quando necessário. Campo sem fonte continua `unknown`.
Descoberta de uma oportunidade pode revelar uma nova página útil do programa
ou operador, mas não cria automaticamente um novo ator nem publica afirmações
sem revisão do contrato correspondente.

O pacote é, portanto, a base para incorporar futuramente histórico de projetos,
investimentos e parcerias sem redesenhar a proveniência. Esta spec apenas
versiona o material adquirido; a modelagem de novas relações de negócio depende
de necessidade comprovada.

### 4.4 Fora do escopo

- procurar novas oportunidades ou ampliar os canais da spec 03;
- construir crawler, CMS ou arquivo web genérico;
- baixar recursivamente todos os links de um domínio;
- interpretar precedência jurídica complexa por LLM;
- substituir os adapters por extrator universal;
- criar OCR ou visão para documentos que hoje não extraem;
- alterar ranking, matching, agentes ou escrita;
- criar fila de revisão humana, pertencente à spec 05; e
- exigir reconstrução integral do catálogo histórico.

## 5. Contrato do pacote

`SourceBundle` é um envelope lógico, não um novo formato de conteúdo:

```json
{
  "schema_version": 1,
  "subject_kind": "opportunity",
  "subject_id": "fapesc:37-2026",
  "source": "fapesc",
  "collected_at": "2026-07-27T12:00:00Z",
  "producer_version": "fapesc-adapter-v1",
  "acquisition_status": "complete",
  "documents": [
    {
      "doc_name": "Edital 37/2026.pdf",
      "units": ["..."],
      "metadata": {
        "role": "base_notice",
        "source_url": "https://...",
        "published_at": "2026-07-01",
        "content_hash": "sha256:...",
        "authority_state": "active",
        "composition_order": 0
      }
    }
  ]
}
```

Campos obrigatórios do envelope:

- `schema_version=1`;
- `subject_kind`: `opportunity`, `investor`, `ict`, `program` ou `agency`;
- `subject_id`: identidade canônica existente;
- `source`;
- `collected_at`;
- `producer_version`; e
- `acquisition_status`: `complete` ou `partial`; e
- ao menos um documento válido.

Campos obrigatórios por documento:

- `doc_name`, `units`, `role` e `content_hash`;
- `source_url` quando a origem a fornece; e
- `authority_state`: `active`, `superseded` ou `contextual`.

`published_at`, `amends_content_hash` e ordem de composição são nullable.
`amends_content_hash`, quando presente, aponta para o `content_hash` do
documento alterado no mesmo bundle; não usa nome de arquivo nem implica que a
emenda substituiu o documento inteiro. A ausência desses dados reduz a
capacidade de precedência; não autoriza inferi-los pelo nome do arquivo.

### 5.1 Identidade e hashes

- `content_hash` identifica o conteúdo de um documento, independentemente de
  URL ou nome.
- `bundle_hash` identifica `schema_version`, sujeito, fonte,
  `acquisition_status`, documentos e metadados documentais materiais
  normalizados. Exclui `collected_at`, `created_at` e `producer_version`, que
  descrevem a execução/produtor e não uma mudança do dossiê.
- Recoletar conteúdo idêntico não cria versão material duplicada.
- Mudança de conteúdo, conjunto de documentos, papel, autoridade ou
  `acquisition_status` cria nova versão. Assim, um bundle `partial` nunca
  impede a persistência posterior do mesmo conjunto confirmado como
  `complete`.
- Hashes novos usam SHA-256. O MD5 legado de `edital_source_docs` permanece
  compatível até migração deliberada; não é usado como prova criptográfica.

Na persistência append-only, `collected_at` registra quando aquela versão
material foi observada pela primeira vez. Reobservações idênticas pertencem à
telemetria de `source_runs`; não atualizam nem duplicam o bundle histórico.

### 5.2 Identidade do sujeito

`subject_id` reutiliza a identidade canônica existente:

- oportunidade: `<source>:<native_id>`, incluindo `web:<url_hash>`;
- investidor: `investidor:<slug>`;
- ICT: `ict:<source>:<slug>`;
- programa: `programa:<slug>`; e
- agência: `agencia:<slug>`.

Não criar um segundo ID específico de bundles.

## 6. Persistência mínima

Adicionar uma única tabela service-role-only:

| Campo | Contrato |
|---|---|
| `id` | UUID |
| `subject_kind`, `subject_id`, `source` | identidade do pacote |
| `bundle_hash` | SHA-256 determinístico |
| `bundle` | JSONB conforme §5 |
| `acquisition_status` | `complete` ou `partial` |
| `collected_at` | timestamp declarado pelo produtor |
| `created_at` | timestamp de persistência |

Restrição única em `(subject_kind, subject_id, bundle_hash)` torna a escrita
idempotente. A tabela é append-only no runtime normal: não atualizar nem apagar
uma versão para publicar outra.

Não criar tabela separada de “versão atual”. A leitura corrente seleciona a
última versão material `complete` do sujeito e aplica a composição da §7.
Bundle `partial` preserva o diagnóstico, mas não substitui a última projeção
completa. A projeção resultante continua sendo gravada em
`edital_source_docs` para que chunking, silver e gold não precisem migrar
juntos.

## 7. Composição e precedência

### 7.1 Regras

1. `base_notice` fornece o texto normativo inicial.
2. `annex` complementa o edital; não o substitui.
3. `amendment` altera somente o que declara alterar.
4. Documento explicitamente consolidado pode substituir os anteriores somente
   quando o produtor oficial o identificar como consolidado.
5. Documento explicitamente superado fica no histórico com
   `authority_state=superseded`, mas sai da projeção factual corrente.
6. FAQ ou página oficial é `contextual` por padrão e não vence edital ou
   retificação normativa.
7. Conteúdo específico de `opportunity_page` vence conteúdo geral de
   `program_page` somente para o mesmo campo da oportunidade.
8. Registro curado não vence silenciosamente documento oficial.
9. Entre valores incompatíveis sem vínculo, papel ou ordem confiáveis, o fato
   fica `conflicting`.

`published_at` sozinho não transforma documento contextual em autoridade
normativa. Nome do arquivo, posição do link ou ordem de download não bastam
para declarar supersessão.

### 7.2 Responsabilidade por campo

Esta spec define precedência documental, não reimplementa todos os extratores.
O produtor de um fato recebe o conjunto ativo e:

- usa a emenda quando ela declara alteração do campo;
- mantém evidências do valor anterior e do novo;
- registra derivação e versão do pacote; ou
- emite `conflicting` quando não puder resolver com as regras acima.

A detecção inicial pode ser limitada aos campos críticos já cobertos pela
proveniência: nome, prazo, elegibilidade, mecanismo/benefício e status.

## 8. Integração com o runtime

### 8.1 Escrita

```text
scraper/staging aprovado
  → adapter atual produz Documento Canônico + metadata
  → valida SourceBundle
  → persiste versão idempotente
  → compõe projeção corrente
  → source_docs.save
  → silver → gold → chunks
```

Falha ao persistir o histórico não deve converter uma coleta válida em perda
de dados na mesma execução. Ela deve ser observável e impedir alegação de
versionamento completo, mas o fallback atual continua disponível.

### 8.2 Leitura e compatibilidade

- `source_docs.load()` mantém o contrato atual.
- Um leitor novo de bundles resolve histórico e versão corrente.
- Consumidores migram apenas quando precisam de histórico ou precedência.
- Registros legados sem bundle permanecem `unknown/legacy`.
- Nenhum consumidor passa a buscar rede durante leitura, match ou escrita.

### 8.3 Proveniência

Novos fatos, relações e chunks apontam para:

- `source_bundle_id` ou referência equivalente estável;
- `bundle_hash`;
- `content_hash` do documento;
- coordenada/trecho já definidos pela spec 01; e
- derivação de precedência, quando aplicável.

Não duplicar o documento completo dentro de `entities.provenance`.

## 9. Rollout

1. criar contrato e fixtures representativas;
2. aplicar migration append-only;
3. iniciar dual-write sem alterar consumidores;
4. validar FAPESC com edital-base + retificação;
5. estender aos demais produtores de oportunidades;
6. versionar as fontes de atores já conhecidas;
7. ativar composição corrente e proveniência da versão; e
8. reconciliar documentação e métricas.

Rollback desliga o dual-write e mantém `edital_source_docs` como hoje. A tabela
histórica pode permanecer sem afetar o runtime.

## 10. Validação proporcional

Reutilizar a suíte `provenance`; não criar harness paralelo.

Casos mínimos:

1. edital-base sem retificação;
2. base + anexo;
3. base + retificação com ordem confiável;
4. duas versões incompatíveis sem precedência resolvível;
5. documento consolidado explicitamente identificado;
6. recoleta idêntica idempotente;
7. mudança material produz nova versão;
8. portal corporativo + página específica de desafio;
9. ator com página oficial + registro curado; e
10. legado sem bundle.

Uma fixture por caso basta. Não testar combinações cartesianas de fonte, papel
e estado. Testes de rede usam doubles locais; nenhuma validação acessa produção
ou credenciais reais.

Métricas diagnósticas:

- sujeitos com ao menos um bundle;
- versões por sujeito;
- documentos por papel;
- fatos críticos ligados a uma versão;
- conflitos e precedências resolvidas; e
- atores com conteúdo oficial ausente.

Nenhum threshold novo é bloqueante sem baseline observado e aprovação.

## 11. Tasks propostas

| Task | Resultado |
|---|---|
| `RT04-T01` | contrato `SourceBundle` e fixtures Web, retificação e ator incompleto |
| `RT04-T02` | migration e repositório append-only idempotente |
| `RT04-T03` | vertical Web: portal/programa + desafio promovido |
| `RT04-T04` | documentos normativos: FAPESC primeiro e demais adapters aplicáveis |
| `RT04-T05` | fontes existentes de ICTs, investidores, programas e agências |
| `RT04-T06` | precedência por campo, conflito e ligação à proveniência |
| `RT04-T07` | métricas diagnósticas, validação final e reconciliação |

T01 precede T02. T03, T04 e T05 podem avançar em paralelo após T02, com pouso
serial nos seams compartilhados. T06 depende de T03–T05. T07 fecha a spec.

Cada task deve caber em um commit funcional mais um commit documental quando
necessário. Se uma task exigir framework novo, tabela adicional ou mudança
ampla de consumidor, deve parar e retornar à governança.

## 12. Critérios de conclusão

1. versões materiais não são sobrescritas;
2. recoleta idêntica é idempotente;
3. papéis documentais e autoridade são explícitos;
4. retificação não apaga o documento anterior;
5. conflito não resolvido chega como `conflicting`;
6. a projeção atual permanece compatível com o pipeline existente;
7. proveniência identifica pacote e documento usados;
8. atores sem fonte suficiente permanecem incompletos, sem conteúdo fabricado;
9. testes não usam rede, produção ou credenciais reais; e
10. não foi criado pipeline, harness ou fonte de verdade paralelos.

## 13. Decisões que exigem retorno ao produto

- documento oficial cuja relação com o edital original não seja demonstrável;
- duas retificações incompatíveis sem ordem confiável;
- FAQ ou página institucional aparentemente contradizendo documento normativo;
- necessidade de baixar uma nova classe de documento não exposta pelos
  produtores atuais;
- proposta de conteúdo sintético para preencher lacuna de ator; ou
- expansão do escopo para revisão humana, OCR, visão ou novos canais.
