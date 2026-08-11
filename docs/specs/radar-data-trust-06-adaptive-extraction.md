# Radar Data Trust 06 — Extração adaptativa evidenciada

**Status:** proposta para aprovação · **Data:** 2026-08-10  
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)  
**Dependências:**
[`radar-data-trust-01-provenance.md`](radar-data-trust-01-provenance.md),
[`radar-data-trust-02-quality-gates.md`](radar-data-trust-02-quality-gates.md),
[`radar-data-trust-04-source-bundles.md`](radar-data-trust-04-source-bundles.md) e
[`radar-data-trust-05-exception-review.md`](radar-data-trust-05-exception-review.md)  
**Consumidores prioritários:**
[`strategic-consultant-v1.md`](strategic-consultant-v1.md) e
[`knowledge-ecosystem-evolution.md`](knowledge-ecosystem-evolution.md)

## 1. Problema comprovado no runtime

O Radar já possui aquisição multicanal, documentos canônicos, estruturação
silver, proveniência, bundles documentais, revisão de exceções e consumidores
capazes de distinguir validade e evidência. A lacuna não é criar outro pipeline;
é tornar a leitura documental capaz de escalar somente quando o formato ou a
qualidade do documento exigirem.

O runtime atual apresenta os seguintes limites concretos:

1. `SourceAdapter` entrega `CanonicalDoc` contendo texto por unidade. Quando a
   extração textual perde layout, tabela ou uma página escaneada, as camadas
   posteriores já não têm informação suficiente para recuperar o conteúdo.
2. FINEP e FAPESC usam `pdfplumber` para texto. Não existe rota produtiva de
   OCR, visão ou parser estrutural de tabelas.
3. FAPESP e Web chegam como texto de HTML; a limpeza preserva conteúdo, mas não
   possui avaliação por campo crítico.
4. O `structurer` usa uma chamada LLM por página para produzir blocos verbatim.
   Páginas podem falhar isoladamente e o documento só é marcado como degradado
   quando a razão de falha ultrapassa 20%. A ausência de uma página crítica pode
   ficar invisível para o consumidor factual.
5. `radar.core.ingestion.edital_extractor` possui contrato tipado, abstenção e
   suíte `extraction`, mas serve ao golden e à avaliação: o gold produtivo não o
   chama.
6. O gold repete produtores próprios para tags, mecanismo, público e constraints.
   Portanto, o contrato mais bem avaliado e o produtor realmente consumido
   podem divergir.
7. `EvidenceRef`, `FactProvenance`, `SourceBundle`, resolução de quote,
   precedência documental e revisão humana já existem. Criar contratos paralelos
   perderia governança já implementada.
8. A SCV1 introduziu `DocumentIntelligence`, `DocumentClaim` e
   `EvidenceReference` no contexto do consultor. Esses tipos são hoje uma
   projeção de consumo; não constituem uma segunda autoridade de extração.
9. A RT05 já expõe os buckets diagnósticos
   `temporal_missing_or_conflicting`, `document_incomplete`,
   `layout_or_ocr_candidates` e `insufficient_for_any_decision`. As métricas são
   puras e locais; não provam ainda que todo bucket possui volume produtivo
   suficiente para justificar uma nova rota.

Conclusão: o problema é uma lacuna de **seleção e convergência de produtores**.
Executar OCR ou visão em todo documento aumentaria custo e complexidade sem
resolver a divergência entre o extrator avaliado e o gold.

## 2. Resultado pretendido

Introduzir um módulo profundo de `AdaptiveDocumentExtraction` que:

- recebe um documento adquirido e os fatos críticos procurados;
- começa pela rota mais simples e barata;
- mede se o resultado é suficiente para esses fatos;
- escala seletivamente para layout/tabelas, OCR e, por último, visão;
- produz afirmações tipadas com `FactProvenance` e `EvidenceRef`;
- explicita campos ausentes, desconhecidos, conflitantes ou sem localizador;
- alimenta gradualmente o gold, `Knowledge`, `CaminhoInovacao` e
  `GroundedWriting` pelo mesmo resultado;
- encaminha falhas materiais para a fila da RT05;
- torna produtores antigos removíveis à medida que cada família de campos for
  promovida.

Fluxo conceitual:

```text
documento adquirido + alvos de extração
→ texto nativo
→ avaliação por campo crítico
→ layout/tabela, se necessário e aplicável
→ OCR, se necessário e aplicável
→ visão, somente se as rotas anteriores não recuperarem o fato visual
→ afirmações + evidências + lacunas
→ composição e precedência do SourceBundle
→ projeção gold/Knowledge/RAG
```

## 3. Princípios

1. **Escalada por necessidade:** nenhuma rota cara é executada apenas porque o
   documento é PDF.
2. **Campo crítico orienta suficiência:** volume de texto não prova que regra,
   prazo ou valor foi recuperado.
3. **Texto antes de layout; layout antes de OCR; OCR antes de visão:** a ordem só
   pode ser invertida quando o tipo do ativo tornar uma etapa inaplicável.
4. **Evidência antes de promoção:** valor crítico sem `EvidenceRef` adequado não
   se torna fato canônico.
5. **Abstenção é resultado válido:** ausência e desconhecido são preferíveis à
   invenção.
6. **Autoridade documental permanece na RT04:** o extrator lê documentos; não
   decide qual versão jurídica prevalece.
7. **Conflito e revisão permanecem na RT05:** a extração emite afirmações; a
   composição detecta conflito e a revisão humana decide exceções.
8. **Temporalidade permanece no read model canônico:** extrair uma data não
   significa declarar uma oportunidade ativa.
9. **Um produtor por fato promovido:** não manter indefinidamente o extrator
   adaptativo e o produtor legado competindo pelo mesmo campo.
10. **Pré-beta proporcional:** fixtures reais, casos difíceis medidos e smokes
    dos consumidores; sem plataforma genérica de documentos.

## 4. Escopo

### 4.1 Sujeitos

A primeira implementação cobre `opportunity` e `channel` usados pelos caminhos
normativo e aberto da SCV1.

Documentos de ICTs, laboratórios, programas e agências podem reutilizar o módulo
depois, mas não bloqueiam a conclusão desta spec.

### 4.2 Alvos prioritários

Na ordem de valor para decisões:

1. regras de elegibilidade e exclusão;
2. prazo, janelas de submissão e declaração explícita de fluxo contínuo;
3. valores de apoio, faixas por projeto e contrapartida;
4. tabelas normativas que sustentem regras, prazos ou valores;
5. evidências e localizadores dos itens anteriores.

Campos contextuais, resumos, temas e redação promocional só entram quando forem
necessários para interpretar um alvo prioritário. O módulo não é um resumidor
universal.

### 4.3 Formatos

- PDF com texto nativo;
- PDF com layout ou tabelas relevantes;
- PDF parcialmente ou totalmente escaneado;
- HTML e texto já normalizado;
- imagem incorporada, somente quando contiver um alvo crítico não recuperável
  pelas rotas anteriores.

## 5. Linguagem e contratos

### 5.1 `DocumentAsset`

Entrada interna do módulo, produzida pela aquisição específica de fonte:

```text
subject_id
source
doc_name
document_role
source_url
published_at
authority_state
media_type
asset_hash
text_units, bytes ou referência local acessível
```

O adapter continua responsável por localizar e adquirir o ativo da fonte. O
`AdaptiveDocumentExtraction` é responsável por interpretar seu conteúdo.
Adapters de texto existentes podem fornecer apenas `text_units`; rotas que
exigem imagem ou layout precisam receber bytes ou referência acessível naquela
execução.

`asset_hash` é SHA-256 do payload exato recebido pelo módulo: bytes quando o
ativo binário estiver disponível; representação canônica dos `text_units` nos
adapters textuais. Ele não substitui `content_hash`/`bundle_hash`, que continuam
identificando documentos e bundles após a normalização da RT04.

A spec não exige object storage. Quando o ativo bruto não estiver acessível, o
resultado deve ser `unavailable` ou parcial, nunca um refetch silencioso fora do
workflow de aquisição.

### 5.2 `ExtractionTarget`

Pedido tipado de um fato que pode mudar uma decisão:

```text
field_path
value_type
required_for: exploration | eligibility | writing
criticality: advisory | decision
```

O conjunto inicial inclui:

- `deadline` e `submission_window`;
- `continuous_flow`;
- `eligible_entities` e `publico_alvo`;
- `eligibility_constraints` e `exclusions`;
- `requires_ict_partner`;
- `funding_amount` e limites por projeto;
- `counterpart`;
- referências de tabela necessárias aos campos anteriores.

### 5.3 `ExtractedClaim`

Representação canônica de uma afirmação documental extraída:

```text
subject_id
field_path
value
provenance: FactProvenance
```

`FactProvenance` já contém:

- `state`;
- `evidence_refs`;
- produtor e versão;
- derivação, quando aplicável;
- validações;
- revisão humana, quando houver.

Não criar um novo score universal de confiança. Para campos críticos:

- `stated` exige evidência textual ou visual recuperável;
- `absent` significa que a rota suficiente inspecionou o escopo e não encontrou
  o fato;
- `unknown` significa que o documento, a rota ou a evidência não permitiram
  decidir;
- `inferred` não pode alimentar gate de elegibilidade nem temporalidade;
- `conflicting` resulta da composição de afirmações incompatíveis, não de uma
  escolha arbitrária do extrator.

O `DocumentClaim` da SCV1 deve ser uma projeção de `ExtractedClaim` para o
consultor. Seu `confidence` atual não autoriza promoção factual.

### 5.4 `ExtractionArtifact`

Resultado idempotente de uma execução:

```text
schema_version
subject_id
asset_hash
bundle_hash, quando disponível
targets_requested
claims
unresolved_targets
structured_blocks
table_fragments
route_trace
status: complete | partial | failed | unavailable
producer_versions
created_at
```

O fingerprint material combina `asset_hash`, schema, produtores e alvos. Mesma
entrada e mesmas versões reutilizam o artefato; mudança material gera nova
versão. O planejamento decide a persistência física mínima, mas ela deve ser
durável no ambiente produtivo e não pode depender apenas de cache de processo.

### 5.5 `RouteTrace`

Registro estrutural e sanitizado das rotas tentadas:

```text
route
reason
pages_or_units
targets_before
targets_resolved
duration_ms
status
```

Não persiste documento bruto, prompt, resposta integral de modelo, segredo ou
stack trace.

## 6. Interface do módulo

O seam externo deve permanecer pequeno:

```python
extract(
    document: DocumentAsset,
    targets: list[ExtractionTarget],
) -> ExtractionArtifact
```

Toda seleção de rota, validação, evidência, custo e fallback fica escondida no
módulo. Consumidores não escolhem OCR, modelo de visão ou parser.

Esse é um seam interno do plano de dados. O contrato da SCV1 permanece:

```python
DocumentIntelligence.ingest(document) -> DocumentIntelligenceResult
```

`DocumentIntelligence` pode chamar `AdaptiveDocumentExtraction` e projetar suas
afirmações para a jornada. O `ConsultantGraph` não conhece
`ExtractionTarget`, `RouteTrace` nem adapters de parser.

O módulo admite adapters internos somente quando há variação real:

- parser de texto/layout;
- OCR;
- visão;
- extrator semântico tipado.

Esses adapters não fazem parte da interface consumida pelo gold ou pelo
`ConsultantGraph`.

## 7. Política de roteamento

### 7.1 Rota 1 — texto nativo

É sempre a primeira rota quando existe texto confiável. Reutiliza HTML limpo,
texto PDF e o structurer atual enquanto o substituto não for promovido.

O resultado é insuficiente quando, para um alvo crítico:

- não há texto na página ou unidade esperada;
- o fato não é encontrado e há sinal de tabela/layout;
- o trecho foi extraído, mas não pode ser localizado;
- data, moeda, percentual ou faixa falha na validação estrutural;
- páginas falharam e podem conter a seção relevante;
- documentos do bundle permanecem incompatíveis.

### 7.2 Rota 2 — layout e tabelas

É acionada quando o texto existe, mas a ordem, colunas, cabeçalhos ou células
podem mudar o significado. Deve preservar, quando disponíveis:

- página;
- título ou seção;
- linhas e colunas;
- caption;
- coordenadas ou identificador de bloco;
- texto verbatim usado como evidência.

Não é obrigatório escolher Docling ou outra biblioteca nesta spec. A escolha
deve ser feita com o menor caso real que prove recuperação superior ao texto
nativo.

### 7.3 Rota 3 — OCR

É acionada por página ou região, não obrigatoriamente pelo PDF inteiro, quando:

- não existe camada textual útil;
- densidade de texto é incompatível com uma página visualmente ocupada;
- o ativo é identificado como escaneado;
- layout recupera a região, mas não o conteúdo textual necessário.

O texto de OCR deve manter referência à página/região e indicar o produtor. OCR
não transforma automaticamente o resultado em `stated`: o quote precisa ser
recuperável e o valor deve passar pelas validações do alvo.

### 7.4 Rota 4 — visão

É último recurso e só pode receber páginas ou recortes selecionados. Aplica-se a
conteúdo visual que OCR/layout não representem suficientemente, como tabela
complexa, diagrama normativo ou imagem com semântica decisória.

Visão nunca é usada para “melhorar” texto já suficiente nem para resumir todo o
documento. Resultado sem evidência/localizador permanece `unknown`.

### 7.5 Parada antecipada

A cascata encerra quando todos os alvos `decision` estão resolvidos com estado e
evidência compatíveis com seu consumidor. Alvos `advisory` não justificam
isoladamente OCR ou visão.

### 7.6 Implementação textual unificada — RT06-T06B

Antes da promoção da T07, a expansão textual enxuta reutiliza os `text_units`,
blocos, coordenadas e hashes já produzidos pelo silver. Uma única passagem
estruturada cobre os alvos prioritários da família inicial e os fatos decisórios
de prazo, janela, fluxo contínuo, valores, contrapartida e referências de
tabela; não há prompt ou chamada por campo.

O produtor textual é dedicado à RT06, usa a factory LLM e retorna envelopes
tipados com `value`, `state` e `evidence`. Omissão é `unknown`; `absent` só é
aceito quando a inspeção do documento permite concluir explicitamente a
ausência. `continuous_flow` exige declaração literal, e `stated` exige quote
resolvido. Constraints continuam no formato autoritativo `{tipo, op, valor}`.

Documentos longos são divididos deterministicamente por seção e limite de
texto, consolidados por documento e só então encaminhados à composição RT04.
Documentos distintos não são condensados, e conflito não é decidido pelo
extrator. Quando a relação de células de uma tabela se perdeu, o claim fica
`unknown` com sinal diagnóstico; não se adiciona OCR, layout, visão,
armazenamento ou backfill nesta task. A promoção permanece separada por família
e depende da T07, com Postgres local e goldens humanos/versionados.

## 8. Validação e suficiência

Cada afirmação passa por validações proporcionais ao tipo:

- datas válidas e coerentes com a linguagem de prazo;
- moeda, percentual e faixas numericamente válidos;
- TRL dentro de `1..9`;
- vocabulários e operadores de constraint já definidos no schema de domínio;
- evidência verbatim ou localização visual recuperável;
- hash do documento e identidade do bundle;
- ausência de inferência em campos de gate;
- autoridade e precedência resolvidas pela projeção da RT04;
- conflito temporal encaminhado ao avaliador da RT05.

Uma validação falha não apaga a afirmação nem promove valor parcial. Ela produz
`unknown`/`partial` e, quando material para o produto, `DataQualityException`
com os códigos existentes:

- `critical_fact_missing`;
- `validation_failed`;
- `evidence_unresolved`;
- `fact_conflict`;
- códigos temporais já vigentes.

Novos `issue_code` só são criados se os existentes não distinguirem uma decisão
operacional real.

## 9. Relação com bundles, conflito e temporalidade

O módulo extrai por documento e versão. Depois:

1. RT04 agrupa documentos no `SourceBundle`;
2. autoridade, emenda, página de oportunidade e programa determinam precedência;
3. valores incompatíveis sem vencedor único tornam-se `conflicting`;
4. RT05 registra e projeta revisão humana quando necessária;
5. o read model temporal calcula `active`, `closed` ou `needs_review`.

Regras mandatórias:

- prazo ausente não significa fluxo contínuo;
- status de listagem não substitui evidência normativa;
- data extraída de documento superado não vence documento ativo;
- uma retificação pode alterar somente um campo;
- extração não promove nem rejeita item da Descoberta;
- revisão humana não treina nem altera prompt automaticamente.

## 10. Integração com o sistema atual

### 10.1 Silver e documento canônico

O `CanonicalDoc` continua sendo o contrato textual dos consumidores existentes
durante a migração. O novo módulo deve produzir ou enriquecer blocos sem fazer o
structurer conhecer fonte específica.

Para rotas não textuais, a aquisição precisa entregar `DocumentAsset` antes de
descartar os bytes. Isso exige ajuste localizado nos adapters de PDF; não exige
object storage como pré-condição.

### 10.2 Gold e `Knowledge`

Campos promovidos passam a ser materializados a partir de `ExtractedClaim`.
Enquanto um campo estiver em shadow, o gold continua usando o produtor vigente e
registra comparação diagnóstica.

Após promoção de uma família de campos, o produtor legado equivalente deve ser
removido. Em particular, `edital_extractor`, `constraints_producer`, heurísticas
de mecanismo e mapeamentos do gold não podem permanecer como autoridades
concorrentes do mesmo fato.

### 10.3 `ConsultantGraph` e caminhos

O `ConsultantGraph` não executa OCR nem escolhe rota. Ele consome pelo contrato
`Knowledge`:

- fatos confirmados;
- evidências;
- lacunas;
- conflitos;
- estado temporal.

O caminho normativo usa essas informações para elegibilidade e próximo passo. O
caminho aberto pode consumir afirmações de HTML e Deep Research, sempre
preservando `source_role` e `review_state`.

### 10.4 `GroundedWriting`

A escrita recebe as mesmas afirmações e evidências usadas para selecionar o
caminho. RAG continua recuperando trechos extensos; `ExtractedClaim` fornece os
fatos estruturados e requisitos que orientam outline, crítica e checklist.

## 11. Persistência, idempotência e reprocessamento

- o artefato canônico vive em uma tabela append-only `document_extractions`;
- a tabela possui, no mínimo, `id`, `fingerprint` único, `subject_id`,
  `asset_hash`, `bundle_hash` nullable, `schema_version`, `status`, `artifact`
  JSONB e `created_at`;
- `artifact` contém o `ExtractionArtifact` completo, incluindo afirmações e
  `route_trace`; não criar tabela por afirmação nesta versão;
- a tabela é service-role-only, com RLS no padrão de `source_bundles` e
  `edital_source_docs`;
- artefatos de extração são imutáveis por fingerprint;
- nova versão de documento gera novo artefato;
- mudança de parser/modelo/schema não sobrescreve resultado anterior;
- falha de rota nova preserva a última projeção saudável;
- reprocessamento pode ser limitado por documento, versão, campo ou rota;
- o gold só troca sua projeção após promoção explícita;
- logs e ledger armazenam categoria e tipo de falha, nunca conteúdo sensível.

Backfill inicial cobre somente oportunidades ativas ou `needs_review` das duas
verticais da SCV1 e os casos representativos do harness. Não há backfill
nacional obrigatório.

O resultado não armazena bytes do documento, prompt ou resposta bruta da LLM.
Durante o pré-beta, o ativo binário pode continuar no mecanismo de aquisição
existente; ausência do ativo para reprocessamento produz `unavailable`. Object
storage permanece evolução posterior, acionada por perda real de durabilidade.

### 11.1 API e segurança

Não há nova API pública nesta spec. Consumidores internos leem a projeção gold
ou `Knowledge`, não `document_extractions` diretamente. Exceções continuam na
API administrativa da RT05.

Conteúdo bruto, URLs internas, prompts, respostas de modelo e erros integrais
não entram em métricas ou logs. Qualquer futura superfície administrativa do
artefato exige autorização separada e payload sanitizado.

## 12. Rollout e rollback

Promoção ocorre por família de campos e consumidor:

1. **baseline:** registrar resultado e falhas do produtor vigente;
2. **shadow:** executar a cascata sem alterar a projeção lida pelo produto;
3. **comparação:** revisar divergências em casos representativos;
4. **promoção:** habilitar o novo artefato para `Knowledge`, elegibilidade ou
   Writing conforme o campo;
5. **remoção:** excluir o produtor legado daquele campo;
6. **expansão:** repetir para a família seguinte.

Rollback troca a projeção de leitura para a última versão saudável. Artefatos e
revisões permanecem append-only. Rollback não reativa produtor legado já removido
sem decisão explícita de incidente.

Flags temporárias podem controlar shadow e promoção, mas devem ser removidas ao
final de cada corte. Não criar matriz permanente de combinações por fonte.

## 13. Observabilidade

Métricas mínimas:

- documentos e páginas por rota;
- razão de parada em texto/layout/OCR/visão;
- alvos resolvidos, ausentes e desconhecidos por `field_path`;
- evidências `exact`, `document_only` e `unresolved`;
- validações reprovadas;
- divergências com produtor vigente durante shadow;
- conflitos e exceções encaminhados à RT05;
- latência e custo por rota;
- reprocessamentos e cache hits por fingerprint.

Os buckets `spec06_signals` da RT05 permanecem como diagnóstico de demanda. Eles
não escolhem automaticamente uma tecnologia nem autorizam promoção.

## 14. Avaliação proporcional

Reutilizar a suíte `extraction` do harness; não criar harness paralelo.

Casos mínimos:

1. PDF com texto nativo e fato crítico localizado;
2. HTML com regra ou prazo e evidência exata;
3. tabela/layout em que texto linear perde associação material, somente se
   existir caso medido;
4. página escaneada, somente se existir caso medido;
5. campo realmente ausente;
6. evidência sem localizador suficiente;
7. retificação que altera um único fato;
8. conflito sem precedência resolvível;
9. falha de rota que preserva projeção saudável;
10. paridade do fato entre `Knowledge`, `CaminhoInovacao` e Writing.

Métricas existentes continuam válidas:

- `presence_accuracy`;
- `value_correctness`;
- `evidence_faithfulness`.

Devem ser acrescentadas, no mesmo harness:

- resolução por alvo;
- qualidade do localizador;
- ganho da rota escalada sobre texto nativo;
- taxa de escalada desnecessária;
- consistência da projeção entre consumidores.

Testes focais, fixtures capturadas e smoke das duas verticais são suficientes.
Suíte exaustiva só é exigida quando a mudança afetar transversalmente o pipeline
ou o contrato compartilhado.

## 15. Não objetivos

- executar OCR ou visão em todo PDF;
- escolher antecipadamente Docling, provedor de OCR ou modelo de visão;
- substituir `SourceBundle`, `EvidenceRef` ou a fila de exceções;
- resolver juridicamente conflitos documentais;
- inferir abertura a partir de ausência de prazo;
- publicar automaticamente uma correção humana como golden;
- treinar ou ajustar modelo automaticamente;
- migrar o KG para Neo4j;
- implementar Graph Builder ou GraphRAG;
- exigir object storage, Kubernetes ou nova plataforma de dados;
- cobrir imediatamente todos os campos, fontes ou atores;
- transformar Discovery em agente LangGraph;
- reabrir os fluxos legados removidos pela SCV1.

## 16. Critérios de aceite da spec

1. Existe um único contrato canônico de afirmação extraída, baseado em
   `FactProvenance` e `EvidenceRef`.
2. `AdaptiveDocumentExtraction` esconde parser, OCR e visão, enquanto
   `DocumentIntelligence.ingest(...)` permanece estável para a SCV1.
3. A cascata começa na rota mais barata e encerra por suficiência dos alvos.
4. Regras, prazos, valores e tabelas preservam evidência e identidade documental.
5. Ausência, desconhecido e conflito não são convertidos em certeza.
6. RT04 e RT05 continuam autoridades de precedência, conflito, revisão e
   temporalidade.
7. O extrator avaliado e o produtor consumido convergem progressivamente.
8. `Knowledge`, caminhos e Writing podem consumir a mesma afirmação sem
   reinterpretar sua origem.
9. Uma rota nova só é promovida quando resolve caso medido melhor que a rota
   anterior.
10. Produtores legados são removidos por família de campos promovida.
11. Falha ou rollback preserva a última projeção saudável.
12. A solução continua simples e operável por um desenvolvedor solo.

## 17. Critérios de conclusão da implementação

A RT06 estará concluída quando:

- pelo menos uma família de fatos críticos da vertical normativa estiver
  produzida ponta a ponta pelo novo módulo e o produtor antigo correspondente
  tiver sido removido;
- a rota de texto estiver integrada ao pipeline produtivo, não apenas ao golden;
- qualquer rota de layout/OCR/visão implementada possuir caso medido que a
  justifique e ganho registrado no harness;
- afirmações, evidências e lacunas forem iguais nos consumidores prioritários;
- conflitos e falhas materiais chegarem ao mecanismo existente da RT05;
- rollout, rollback e reprocessamento seletivo estiverem demonstrados com
  fixtures representativas;
- documentação autoritativa de schema e fontes estiver reconciliada com o
  runtime final.
