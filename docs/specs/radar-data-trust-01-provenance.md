# Radar Data Trust 01 — Proveniência de ponta a ponta

**Status:** proposta para aprovação · **Data:** 2026-07-21
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)
**Contrato anterior:**
[`radar-data-trust-00-relevance-contract.md`](radar-data-trust-00-relevance-contract.md)
**Ordem:** 01 · **Bloqueia:** confiança visível, revisão por exceção e pacotes documentais
**Perfis afetados:** usuário de produto, operador e usuário técnico
**Impacto:** alto; domínio, ingestão, gold, APIs, agentes, frontend e avaliação

---

## 1. Problema comprovado

O runtime já preserva partes importantes da proveniência:

- `evidence_package` registra identidade, coletor e texto da Descoberta;
- Documento Canônico mantém `doc_name` e unidades lógicas;
- silver mantém `doc`, `page`, `section_path`, `kind` e texto verbatim;
- `Extracted[T]` diferencia `stated`, `inferred` e `absent`, com uma substring
  textual de evidência;
- a suíte `extraction` mede faithfulness dessa substring;
- `edital_chunks` guarda `source_file` e `page_range`; e
- promotion runs e events registram a rota técnica.

Essas peças não formam ainda um contrato end-to-end. O gold materializa valores
em colunas, `metadata`, constraints e relações sem uma forma homogênea de
responder qual documento, versão, página e trecho sustentam cada fato. Uma
string de evidência solta também pode ser ambígua, repetir-se em vários anexos
ou perder o vínculo após normalização.

Consequências:

- Explorar pode responder corretamente sem conseguir citar a origem precisa;
- o usuário não distingue afirmação documental, inferência e default;
- uma relação incorreta no KG é difícil de auditar;
- revisão humana precisa reler documentos inteiros; e
- backfills ou mudanças de extrator podem alterar valores sem uma derivação
  comparável.

## 2. Resultado pretendido

Para todo fato crítico novo publicado após esta spec, o sistema deve responder:

1. qual valor foi materializado;
2. qual seu estado factual;
3. qual versão do documento o sustenta;
4. onde está o trecho verificável;
5. qual produtor e versão derivaram o valor;
6. quais validações foram executadas; e
7. se houve revisão humana, conflito ou override.

Esse contrato deve sobreviver ao caminho:

```text
evidence package / source adapter
  → Documento Canônico
  → silver
  → extração e normalização
  → entities / entity_relationships / match_chunks / edital_chunks
  → API e tools
  → Explorar, Radar e Escrita
```

O primeiro incremento cobre fatos que alteram decisão, vigência, elegibilidade
ou explicação. Não exige proveniência campo a campo para todo texto descritivo.

## 3. Escopo inicial de fatos críticos

### 3.1 Entidade `edital`

| Grupo | Campos/caminhos |
|---|---|
| participação | `eligible_entities`, `constraints`, `requisitos_texto` quando apresentado como requisito |
| aderência | `themes`, `setores`, `tecnologias_tags`, `trl_range` |
| mecanismo | `mechanism`/`mecanismo`, `counterpart`, `requires_ict_partner` |
| financeiro | `funding_amount`, `ticket_min`, `ticket_max` |
| temporal | `status`, `deadline`, `verificado_em` |
| identidade | `name`, agência operadora e programa subordinante |

`status` e `deadline` continuam pertencendo ao pipeline temporal, não ao schema
`EditalExtraction`. Esta spec exige proveniência no produtor autoritativo; não
move sua responsabilidade para a LLM de extração.

### 3.2 Atores do Ecossistema

O vertical slice também cobre os campos que sustentam descoberta e explicação
de atores, sem exigir que possuam corpus suficiente para RAG:

| `kind` | Fatos críticos iniciais |
|---|---|
| `ict` | identidade, vínculo/acreditação, localização, competências e URL oficial |
| `investidor` | identidade, tese/setores, estágio, geografia, ticket quando declarado e URL oficial |
| `programa` | identidade, operador, finalidade, público e vínculo com oportunidades |
| `agencia` | identidade e relações operacionais publicadas |

Campos atualmente derivados por LLM ou curadoria básica começam como
`legacy/unknown` até resolverem evidência conforme este contrato. Origem oficial
não prova que a extração esteja correta, completa ou atual.

### 3.3 Relações

Primeiro conjunto:

- `operado_por`;
- `subordinado_a`; e
- `exige_parceria_com` quando houver alvo específico e evidência suficiente; e
- `credenciada_por`, com referência verificável ao vínculo EMBRAPII.

### 3.4 Chunks

- `match_chunks`: cada chunk deve apontar para documento, página/bloco e versão
  silver/canônica;
- `edital_chunks`: preservar `source_file` e `page_range` existentes e acrescentar
  hash/versão de origem no metadata quando ausentes; e
- contextualização e embedding nunca substituem o texto-fonte como evidência.

ICTs e investidores atualmente não possuem conteúdo suficiente para um corpus
RAG defensável. Esta spec não exige chunks para torná-los `in_scope` nem trata
ausência de chunks como falha de ingestão. Até a spec 04 adquirir e versionar
páginas/fontes suficientes, Explorar deve usar campos estruturados com sua
proveniência e declarar lacunas; descrições ou chunks sintéticos são proibidos.

## 4. Modelo canônico

### 4.1 Estado factual

```text
stated      valor declarado explicitamente em fonte autorizada
inferred    valor derivado por regra/modelo, sem declaração textual equivalente
absent      campo procurado no escopo documental disponível e não encontrado
conflicting fontes autorizadas sustentam valores incompatíveis sem precedência resolvida
unknown     legado ou material insuficiente para avaliar presença
```

Regras:

- `absent` é mais forte que `unknown`: exige escopo de busca conhecido;
- `inferred` não pode ser usado como evidência de elegibilidade dura;
- `conflicting` impede apresentação de um único valor como certo;
- defaults de domínio devem declarar `producer.kind=default`, nunca `stated`; e
- o estado de uma oportunidade não substitui a decisão de relevância da spec 00.

### 4.2 `EvidenceRef`

Contrato lógico compartilhado por domínio, persistência, APIs e evals:

```json
{
  "schema_version": 1,
  "source": "finep",
  "native_id": "745",
  "edital_id": "finep:745",
  "source_url": "https://...",
  "document": "Edital.pdf",
  "page": 17,
  "block_idx": 143,
  "section_path": ["7. Elegibilidade", "7.2 Proponentes"],
  "quote": "Poderão participar empresas brasileiras...",
  "canonical_content_hash": "sha256:...",
  "silver_source_hash": "sha256:...",
  "collected_at": "2026-07-21T12:00:00Z",
  "locator_quality": "exact | document_only | unresolved"
}
```

Requisitos:

- `quote` é verbatim e limitado a contexto suficiente para inspeção; não é um
  resumo produzido pela LLM;
- `page` é 1-based e pode ser nulo para HTML sem unidade paginada;
- `block_idx` referencia o bloco silver quando existir;
- ao menos um dos hashes identifica a versão recuperável;
- `document_only` é permitido durante compatibilidade, mas não satisfaz gates
  futuros de evidência exata; e
- `unresolved` registra falha sem fabricar coordenadas.

### 4.3 `FactProvenance`

```json
{
  "state": "stated",
  "evidence_refs": [],
  "producer": {
    "kind": "adapter | deterministic | llm | human | default | backfill",
    "name": "edital_extractor",
    "version": "2",
    "model": "gpt-4o-mini",
    "prompt_version": "extraction-v2"
  },
  "derivation": {
    "inputs": ["eligible_entities"],
    "rule": "canonicalize_eligible_entities:v1"
  },
  "validations": [
    {"name": "quote_is_verbatim", "status": "passed"}
  ],
  "review": null,
  "updated_at": "2026-07-21T12:00:00Z"
}
```

O campo `model` é omitido para produtores sem modelo. Nenhuma chave, prompt
integral, chain-of-thought ou dado privado é persistido.

### 4.4 Confiança

O contrato v1 não introduz probabilidade numérica. Confiança operacional é
derivada de:

- estado factual;
- qualidade do locator;
- resultados de validação;
- quantidade e independência das evidências;
- conflito; e
- revisão humana.

Uma futura classificação `high/medium/low` deve ser função determinística desses
sinais. A LLM extratora não pode atribuir a si própria confiança autoritativa.

## 5. Captura e resolução de evidência

### 5.1 Descoberta e Documento Canônico

- `evidence_package` continua sendo envelope de staging e deve referenciar todos
  os documentos aprovados, sem virar uma segunda fonte de verdade do gold.
- Source Adapter continua sendo a fronteira individualizada → agnóstica.
- Documento Canônico deve preservar nome, ordem, unidade lógica, hash e metadata
  de coleta suficientes para resolver um `EvidenceRef`.
- Conteúdo bruto e documentos continuam em seus seams atuais; a proveniência
  guarda referências e trechos limitados, não cópias integrais.

### 5.2 Silver

O bloco silver já possui as coordenadas fundamentais. A implementação deve:

- manter `doc`, `page`, `idx`, `section_path`, `kind` e `text` estáveis para uma
  mesma combinação de conteúdo e versão do structurer;
- tornar `source_hash`, versão do schema, prompt e modelo acessíveis ao resolver;
- localizar a substring de `Extracted.evidence` nos blocos da mesma oportunidade;
- marcar ambiguidade quando o mesmo trecho ocorrer em múltiplos locais; e
- nunca escolher silenciosamente a primeira ocorrência quando isso mudar a
  interpretação.

### 5.3 Extração, normalização e derivação

- `Extracted.evidence` permanece temporariamente para compatibilidade e ganha
  `evidence_refs` ou um adaptador explícito para o contrato novo.
- Normalizadores propagam as referências do valor cru ao valor canônico.
- Tags ou constraints produzidas por LLM carregam as evidências usadas e o
  produtor; sem trecho suficiente, são `inferred` ou `unknown` conforme regra.
- Uma regra determinística derivada de outro campo aponta para o campo de
  entrada em `derivation.inputs`; não duplica citação como se fosse extração.
- Overrides humanos são append-only no histórico operacional, com ator e data;
  o valor corrente aponta para a revisão que o estabeleceu.

## 6. Persistência gold

### 6.1 Decisão de armazenamento v1

O primeiro incremento usa colunas JSONB explícitas, aditivas e versionadas:

```sql
entities.provenance              jsonb not null default '{}'
entity_relationships.provenance jsonb not null default '{}'
```

Em `entities.provenance`, as chaves são paths estáveis de fatos, por exemplo
`deadline`, `constraints.porte` ou `metadata.trl_range`, e os valores seguem
`FactProvenance`. Em relações, a coluna descreve a própria aresta.

Motivos:

- acompanha o upsert atômico atual do gold;
- evita uma tabela polimórfica de fatos antes de conhecer os padrões reais de
  consulta;
- permite evolução versionada e backfill parcial; e
- não altera RLS ou cardinalidade das tabelas públicas.

Uma futura normalização em tabela própria exige evidência de limites de volume,
consulta ou auditoria e uma spec/ADR; não deve ser antecipada nesta entrega.

### 6.2 Chunks

Adicionar a `match_chunks` coordenadas mínimas compatíveis com silver:

```text
document, page, silver_block_idx, source_hash
```

Para `edital_chunks`, reutilizar `source_file`, `page_range` e `metadata`,
registrando `canonical_content_hash`, versão do chunker e versão de
contextualização. O texto contextualizado usado para embedding não é armazenado
como substituto do chunk original nem citado ao usuário como fonte.

### 6.3 RLS e exposição

- Escrita continua somente por service role.
- Leitura segue as policies existentes de catálogo público autenticado.
- Evidência não pode conter segredos, headers, HTML integral, dados pessoais
  desnecessários nem paths locais.
- A API retorna somente o subconjunto público do contrato; metadata operacional
  sensível permanece administrativa.

### 6.4 Contrato por origem

O tipo `FactProvenance` é comum, mas as evidências válidas variam conforme a
autoridade da origem:

| Origem | Produtor mantido | Evidência mínima aceitável no primeiro rollout |
|---|---|---|
| FINEP, FAPESP e FAPESC | adapters e ingest gold atuais | `EvidenceRef` documental com trecho, documento, página/bloco quando disponível e hash |
| Web promovida | promotion run + adapter web atuais | evidence package aprovado, URL/documento coletado, trecho e hash da versão |
| EMBRAPII | scraper, produtor específico e artefato versionado atuais | arquivo/registro, chave estável, página oficial quando preservada, hash e versão do produtor; campos não resolvidos ficam `unknown` |
| Investidores e programas existentes | JSONs versionados atuais, com extração LLM e/ou curadoria básica | path lógico, chave, página oficial disponível, hash/commit, `producer.kind=llm|human` e revisão; nenhuma confiança presumida |
| Legado | linha gold existente | `state=unknown` e qualidade `legacy` até resolução válida |

Regras:

- nenhum adapter ou catálogo é substituído por esta spec;
- os JSONs existentes não precisam atravessar Documento Canônico/silver apenas
  para simular uma origem documental;
- `document_only` ou referência de registro curado é legítima quando o tipo de
  origem assim determina, mas a UI não a rotula como citação de página; e
- uma futura uniformização de produtores exige spec própria e equivalência do
  resultado, não pode entrar como efeito colateral da provenance; e
- cada `kind` passa por validação própria de relevância e completude; não usar o
  classificador de oportunidade nem dispensar validação por estar “curado”.

## 7. Contratos de consumo

### 7.1 API

Fichas de oportunidade e respostas factuais passam a poder retornar:

```json
{
  "value": "31/10/2026",
  "state": "stated",
  "citations": [
    {
      "document": "Edital.pdf",
      "page": 17,
      "quote": "...",
      "source_url": "https://...",
      "collected_at": "2026-07-21T12:00:00Z"
    }
  ]
}
```

Compatibilidade:

- campos escalares existentes não são removidos no primeiro rollout;
- proveniência entra em envelope ou campo adicional versionado;
- consumidores antigos continuam funcionando;
- ausência de proveniência em legado aparece como `unknown`, nunca como
  evidência vazia implicitamente confiável.

### 7.2 Explorar

- Tools factuais retornam fatos junto de citações estruturadas.
- A síntese referencia as evidências usadas, não uma lista genérica de fontes.
- Afirmações `inferred` são qualificadas como inferência.
- `conflicting` apresenta os valores e documentos em conflito ou se abstém.
- Resposta sem suporte suficiente declara limitação e não completa por memória
  paramétrica do modelo.

### 7.3 Radar / match

- Constraints duras só eliminam quando o fato possui estado permitido pelo
  contrato existente de elegibilidade e evidência rastreável.
- Cards podem expor “por que combina” e “o que falta confirmar” com citações.
- Score de afinidade não é convertido em confiança factual.

### 7.4 Escrita

- Chunks recuperados mantêm `source_file` e `page_range` até as tools/agente.
- Texto gerado pode citar o edital, mas não apresenta contexto sintético do
  contextual retrieval como citação documental.
- Falta de provenance gold não impede retrieval de chunk legado; apenas aparece
  como qualidade reduzida até reindex/backfill.

## 8. Validação e contenção

### 8.1 Validadores mínimos

- `quote_is_verbatim`: trecho existe no bloco/documento versionado;
- `locator_resolves`: documento e coordenada resolvem para artefato recuperável;
- `hash_matches`: versão citada corresponde ao conteúdo armazenado;
- `state_consistent`: `stated` exige evidência; `absent` não carrega valor;
- `producer_complete`: produtor e versão presentes;
- `deadline_consistent`: prazo normalizado é compatível com o trecho;
- `money_consistent`: faixa mínima/máxima e unidade não se contradizem;
- `relationship_supported`: aresta possui evidência ou origem curada explícita.

### 8.2 Resultado de qualidade

```text
publishable   fatos críticos obrigatórios passaram
review        ambiguidade, locator parcial ou baixa sustentação
conflicting   fontes incompatíveis sem precedência resolvida
blocked       evidência fabricada, hash inválido ou contrato inconsistente
legacy        registro anterior à spec, ainda não revalidado
```

Esse estado não substitui `radar_ready`/`rag_ready`; é um eixo adicional de
qualidade. A spec 05 definirá UX e fila de revisão. Até lá, estados não
publicáveis devem permanecer visíveis ao operador e não ganhar aparência de
certeza no produto.

## 9. Backfill e compatibilidade

### 9.1 Registros existentes

- Migration adiciona colunas/metadata com defaults seguros.
- Entidades antigas recebem estado agregado `legacy`, não `stated`.
- Backfill pode resolver evidência apenas quando Documento Canônico/silver e
  hashes correspondentes estiverem disponíveis.
- Correspondência textual deve ser exata e não ambígua; caso contrário registra
  `locator_quality=unresolved`.
- Nenhum backfill usa o valor gold como prompt para fabricar uma citação
  semanticamente parecida.

### 9.2 Dual write

1. produtores começam a gravar contrato legado e proveniência nova;
2. shadow validation mede completude e divergência;
3. APIs expõem proveniência sem exigir cobertura total do legado;
4. novos registros passam a exigir invariantes aceitos;
5. remoção do campo textual legado `evidence` só ocorre em spec posterior e
   após busca de todos os consumidores.

Rollback desliga leitura obrigatória do contrato novo e mantém dados aditivos.
Não apaga provenance já capturada nem regride registros novos para falsa certeza.

### 9.3 Equivalência do gold e proteção dos consumidores

Antes de qualquer consumidor exigir provenance, cada origem deve passar por uma
comparação old-path × dual-write usando a mesma entrada. A projeção canônica
deve permanecer equivalente, desconsiderando somente:

- novas colunas/keys de proveniência;
- timestamps operacionais esperados; e
- IDs físicos gerados, quando a comparação puder usar a chave natural.

A comparação cobre, conforme aplicável:

- `(source, native_id, kind)` e identidade pública;
- campos escalares, arrays, constraints e metadata preexistentes;
- relações por `(source_entity, target_entity, type)`;
- quantidade, texto e coordenadas preexistentes de `match_chunks` e
  `edital_chunks`;
- hashes dos inputs de embedding, modelo e dimensionalidade, sem exigir igualdade
  bit a bit quando um reprocessamento externo legítimo ocorrer; e
- outputs dos gates de matching, RAG e Explorar afetados.

Fixtures obrigatórias: ao menos um caso FINEP, FAPESP, FAPESC, Web promovida,
EMBRAPII e cada catálogo JSON curado participante. Divergência fora dos campos
novos interrompe a task: ou é regressão, ou exige alteração explícita da spec e
novo baseline aprovado.

O rollout preserva a direção de dependência:

```text
schema aditivo
  → produtor em dual-write
  → shadow validation e equivalência
  → API com leitura opcional/fallback
  → superfície habilitada
  → enforcement para registros novos
```

Matching, ranking, retrieval e escrita não passam a depender de provenance na
mesma task que inaugura sua escrita.

### 9.4 Spikes não são caminho de produção

Implementação desta spec acontece nos módulos, migrations e testes canônicos.
Um spike só é permitido para hipótese técnica incerta e deve ser descartável:

- nenhum import produtivo aponta para `spikes/`;
- nenhuma tabela ou artefato gerado pelo spike vira autoridade operacional;
- o spike não mantém adapter, ingest ou índice paralelo; e
- resultado aprovado é reimplementado no caminho canônico, com teste e contrato.

Resolver quote → bloco, comparar OCR/layout ou medir backfill são candidatos a
spike. Adicionar `EvidenceRef`, dual-write e APIs não são: pertencem diretamente
às tasks RT01.

## 10. Avaliação

### 10.1 Evals exigidas nesta entrega

- estender `extraction` para medir resolução de locator além da substring;
- medir completude de proveniência por campo crítico;
- validar propagação raw/silver → gold sem trocar documento ou versão;
- avaliar precisão das relações cobertas;
- medir completude e evidência de ICTs e investidores separadamente;
- testar resposta factual do Explorar com citações required/forbidden;
- verificar que inferência e conflito são verbalizados corretamente; e
- garantir que chunks preservam arquivo/página/hash após reindex.

### 10.2 Casos obrigatórios

- trecho único e exato;
- trecho repetido em duas páginas;
- HTML sem página;
- PDF com múltiplos anexos;
- valor normalizado diferente do texto, como moeda ou data;
- valor derivado deterministicamente;
- campo ausente;
- evidências conflitantes;
- registro legado sem silver recuperável; e
- ICT EMBRAPII com registro oficial incompleto e investidor com campo LLM sem
  evidência recuperável;
- retificação, inicialmente como conflito até a spec 04 definir precedência.

Os thresholds e a promoção a gates oficiais pertencem à spec
`radar-data-trust-02-quality-gates.md`. Esta implementação deve produzir as
métricas completas antes de qualquer threshold ser inventado.

## 11. Observabilidade

Por run/fonte, registrar sem conteúdo sensível:

- fatos críticos produzidos;
- percentual `stated` com locator exato;
- locators ambíguos ou não resolvidos;
- fatos `inferred`, `absent`, `conflicting` e `unknown`;
- falhas por validador;
- entidades/arestas/chunks publicados, revisados ou bloqueados;
- versão de produtor, prompt/modelo e hashes de entrada; e
- duração e custo quando houver chamada externa.

Logs não devem imprimir o documento integral, chaves, URLs assinadas ou payloads
que excedam o trecho de evidência permitido.

## 12. Plano de tasks

Cada task resulta em commit delimitado e validação proporcional ao risco. A
decomposição por origem impede que uma mudança grande em todos os produtores
seja entregue e auditada como um único bloco.

| Task | Resultado |
|---|---|
| `RT01-T01` | tipos `EvidenceRef`/`FactProvenance` e compatibilidade pura |
| `RT01-T02` | projeção de equivalência e fixtures mínimas antes da mudança |
| `RT01-T03` | resolução quote → Documento Canônico/silver |
| `RT01-T04` | migration aditiva e persistência gold sem consumidor novo |
| `RT01-T05` | vertical slice FINEP em dual-write |
| `RT01-T06` | FAPESP, FAPESC e Web no mesmo contrato validado |
| `RT01-T07` | ICTs EMBRAPII com proveniência própria, sem RAG artificial |
| `RT01-T08` | investidores, programas e agências existentes reclassificados como legado/validado por campo |
| `RT01-T09` | coordenadas e versões dos chunks de escrita preservadas |
| `RT01-T10` | API e tools do Explorar com leitura opcional/fallback |
| `RT01-T11` | citações e estados factuais na superfície do produto |
| `RT01-T12` | backfill amostral, shadow metrics e equivalência por origem |
| `RT01-T13` | evals proporcionais, suíte final e reconciliação documental |

Os planos executáveis vivem em
`docs/execution/radar-data-trust/plans/01-provenance/` e são a autoridade sobre
arquivos, comandos e ordem operacional de cada task.

## 13. Regras para implementação delegada

O pacote entregue a DeepSeek, Kimi ou outro implementador deve conter apenas uma
task RT01 por vez e incluir:

- commit-base e arquivos em escopo;
- trechos desta spec que são invariantes;
- contratos de entrada/saída;
- migrations permitidas;
- testes e comandos obrigatórios;
- arquivos protegidos e não objetivos;
- formato do relatório de implementação; e
- regra de parada diante de divergência de runtime.

O implementador não pode simplificar `conflicting` para `absent`, substituir
proveniência por score de confiança ou alterar o dono de deadline/status sem
revisão da spec.

## 14. Não objetivos

- modelar ainda a precedência completa de retificações;
- exigir provenance retroativa perfeita para todo o catálogo;
- criar um banco de grafo ou tabela universal de fatos;
- citar raciocínio interno ou chain-of-thought;
- guardar documentos completos dentro de `entities.provenance`;
- fazer OCR ou visão em todos os documentos;
- definir thresholds finais de todos os gates;
- redesenhar matching, ranking ou escrita; e
- substituir revisão humana por confiança automática.

## 15. Critérios de aceite da spec

Esta spec está pronta para implementação quando forem aprovados:

1. estados factuais e diferença entre `absent` e `unknown`;
2. forma de `EvidenceRef` e `FactProvenance`;
3. escopo inicial de fatos e relações;
4. persistência JSONB aditiva no gold;
5. política de compatibilidade e backfill legado;
6. ausência de score numérico autodeclarado; e
7. matriz de coexistência e evidência diferenciada por origem;
8. equivalência obrigatória do gold antes da leitura nova;
9. uso de spike apenas para hipótese descartável; e
10. ordem das tasks `RT01-T01` a `RT01-T13`.

## 16. Critérios de conclusão da implementação

A spec só pode ser marcada como vigente quando:

- 100% dos fatos críticos **novos** tiverem estado factual e produtor;
- todo fato novo `stated` possuir ao menos um `EvidenceRef` resolvível e verbatim;
- relações cobertas declararem evidência ou origem curada explícita;
- match e RAG preservarem coordenadas de origem após reindex;
- APIs e Explorar distinguirem estados sem quebrar consumidores legados;
- registros anteriores aparecerem como `legacy/unknown` até backfill válido;
- fixtures de todas as origens da §9.3 comprovarem equivalência do gold fora
  dos campos aditivos;
- evals cobrirem os casos da §10 e não houver regressão nos gates existentes;
- migrations, RLS, idempotência e rollback tiverem sido validados;
- nenhum runtime, import ou fonte de verdade depender de `spikes/`; e
- documentação autoritativa estiver reconciliada com o runtime observado.
