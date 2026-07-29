# Spec-mãe — Radar Data Trust

**Status:** vigente (specs 00–05 concluídas) · **Data:** 2026-07-29
**Documento-pai:** [`system-coherence.md`](system-coherence.md)
**Família de specs:** `radar-data-trust-NN-<capacidade>.md`
**Perfis afetados:** usuário de produto, operador e usuário técnico
**Impacto:** alto e incremental; programa de confiabilidade do plano de dados

---

## 1. Função deste documento

Esta é a spec-mãe do programa **Radar Data Trust**. Ela fixa a tese do produto,
os riscos, a arquitetura-alvo, os invariantes, as métricas e a ordem das specs
filhas que tornam confiável o caminho:

```text
Descoberta → documentos → extração → gold → Explorar / Radar / Escrita
```

Ela não autoriza, sozinha, migrations ou mudanças de runtime. Cada entrega é
governada por uma spec filha numerada, com comportamento, migração, rollout,
testes e critérios de conclusão próprios.

### 1.1 Convenção obrigatória de nomenclatura

Todas as specs executáveis deste programa usam o prefixo da spec-mãe:

```text
radar-data-trust-00-relevance-contract.md
radar-data-trust-01-provenance.md
radar-data-trust-02-quality-gates.md
...
```

Uma spec sem o prefixo `radar-data-trust-` não é filha deste programa. O número
indica dependência lógica, não necessariamente um único PR nem execução
estritamente serial.

## 2. Problema e hipótese

O Radar de Editais atende startups e pequenas e médias empresas de base
tecnológica. Para esse público, o ativo central não é o chatbot isoladamente,
mas um mapa atual, pesquisável e explicável das oportunidades **e dos atores**
relevantes para inovação.

Os três resultados do produto herdam a qualidade desse mapa:

- extração ou relações incorretas degradam Explorar e matching;
- aquisição, estruturação ou chunking ruins degradam a escrita com RAG;
- cobertura insuficiente torna irrelevante um ranking tecnicamente correto; e
- ausência de proveniência impede distinguir dado confiável de síntese plausível.

O sistema já possui staging, gate humano, bronze, Documento Canônico, silver,
gold relacional, índices de recuperação e harness de avaliação. A hipótese
deste programa é que a confiabilidade pode evoluir **incrementalmente**, sem
substituir o data plane, adicionando contratos verificáveis e contenção de erro
entre essas camadas.

Princípio central:

> **O Radar não precisa conhecer todo o Brasil; precisa declarar o que procura,
> medir o que monitora e provar de onde veio cada afirmação relevante.**

## 3. Tese de cobertura do produto

O programa não pretende catalogar todos os órgãos públicos, chamadas ou
instrumentos existentes no país. O universo-alvo é definido pelo benefício e
pelo público elegível da oportunidade, não pelo nome ou tipo da instituição.

Incluem-se, em princípio, oportunidades acionáveis para startups e PMEs de base
tecnológica, como subvenção econômica, apoio não reembolsável, desafios de
inovação, programas de aceleração/incubação com benefício concreto e chamadas
de cooperação tecnológica que aceitem empresas.

Excluem-se, em princípio, bolsas exclusivamente acadêmicas, crédito
convencional, compras públicas sem componente de inovação, eventos genéricos e
instrumentos sem caminho de participação empresarial. Uma instituição que
publica conteúdo fora do escopo não é excluída como fonte: cada oportunidade é
classificada individualmente.

O contrato normativo completo e os casos limítrofes vivem em
[`radar-data-trust-00-relevance-contract.md`](radar-data-trust-00-relevance-contract.md).

## 4. Estado atual reutilizável

| Capacidade existente | Papel no programa | Lacuna principal |
|---|---|---|
| DOU + busca web + hubs opcionais | descoberta multicanal inicial | cobertura ainda não possui control plane nem medida de frescor |
| `discovered_opportunities` | staging antes da publicação | revisão decide a oportunidade, não cada fato extraído |
| `evidence_package` | envelope versionado de coleta | origem e confiança ainda são grosseiras |
| promotion runs e events | progresso técnico e retry auditável | não há estado de qualidade por campo |
| bronze + Documento Canônico durável | reprodução do conteúdo adquirido | oportunidade ainda não é pacote documental versionado |
| silver com `doc`, `page`, `section_path` e texto verbatim | coordenadas para evidência | coordenadas não chegam de forma uniforme ao gold e às APIs |
| `Extracted[T]` com `state` e `evidence` | abstenção e trecho textual | falta referência estruturada, conflito e propagação end-to-end |
| `entities`, `entity_relationships`, `match_chunks` | catálogo e matching ativos | fatos e arestas não expõem proveniência homogênea |
| `edital_chunks` | RAG de escrita | qualidade documental não é uma readiness explícita para o usuário |
| harness unificado e goldens | base de gates reproduzíveis | cobertura pequena e poucas suítes bloqueantes |

Este inventário é uma fotografia do início do programa. Cada spec filha deve
revalidar o runtime que toca e reconciliar esta tabela ao concluir.

### 4.1 Snapshot de avaliação no início do programa

| Suíte | Casos versionados | Estado operacional inicial |
|---|---:|---|
| `triage` | 122 | diagnóstica obrigatória quando a triagem muda; sem threshold aceito |
| `extraction` | 10 | gate; baseline aceito de presença/abstenção `0.95` |
| `structurer` | 12 | diagnóstica; sem threshold aceito |
| `opportunity_type` | 6 | diagnóstica; sem threshold aceito |
| `explore` | 4 | diagnóstica, com modo E2E conectado opcional |

As contagens medem o corpus, não sua representatividade. A spec 02 deve ampliar
e estratificar os casos antes de promover novos gates ou alegações de cobertura.

### 4.2 Matriz de coexistência por origem

O programa estende os produtores atuais; não substitui suas autoridades nem
obriga origens diferentes a fingirem o mesmo nível de evidência.

| Origem atual | Autoridade preservada | Proveniência v1 esperada | Regra de coexistência |
|---|---|---|---|
| FINEP, FAPESP e FAPESC | Source Adapter → Documento Canônico → silver → gold | documento, página/bloco, trecho e hashes disponíveis | adapters e IDs atuais permanecem; proveniência é dual-write aditivo |
| Web/Descoberta | staging aprovado → evidência/bronze web → adapter → pipeline nativo | pacote promovido, URL/documento, trecho e versão coletada | nenhum candidato pula o gate humano ou cria gold paralelo |
| EMBRAPII | scraper, catálogo/artefato versionado e produtor específico existentes | registro, página oficial quando recuperável, hash e identidade do produtor | cobertura ampla do portal não prova correção/completude; ICTs passam por contrato de confiança próprio |
| Investidores e programas | JSONs versionados atuais, parcialmente produzidos por extração LLM e curadoria básica | arquivo, chave, página oficial disponível, hash/commit, modelo/produtor e revisão | “curado” não significa validado; passam por contrato próprio de relevância, completude e evidência |
| Registros gold anteriores | tabelas atuais e seus produtores históricos | `legacy/unknown` até backfill comprovável | ausência de proveniência nova não reclassifica nem apaga o registro |

Uma referência de curadoria é evidência legítima, mas diferente de uma citação
de edital. O contrato público deve comunicar essa diferença em vez de inventar
`page`, `quote` ou confiança documental para JSONs curados.

ICTs e investidores não usam o **mesmo classificador** de uma chamada porque
não são oportunidades acionáveis. Isso não os isenta de triagem: cada `kind`
tem critérios próprios de inclusão, campos críticos, proveniência e golden.
Conteúdo insuficiente para chunks/RAG é um gap mensurável, não autorização para
fabricar descrições ou embeddings a partir de inferência.

## 5. Arquitetura-alvo

```text
FONTES DE OPORTUNIDADE                    FONTES DE ATORES
adapters, DOU, busca, hubs                EMBRAPII, páginas oficiais, JSONs
            ▼                                         ▼
   staging + gate editorial               validação própria por kind
            └──────────────────┬──────────────────────┘
                               ▼
                  pacote de fontes versionado
      editais/anexos/retificações + registros/páginas de atores
                               ▼
              extração/curadoria com proveniência por fato
                           ▼
          validação determinística + conflito + quarentena
                           ▼
              Documento Canônico / silver / gold
                           ▼
       ┌───────────────────┼────────────────────┐
       ▼                   ▼                    ▼
   Explorar            Radar / match         Escrita / RAG
  com citações       com elegibilidade      com chunks rastreáveis
       └───────────────────┬────────────────────┘
                           ▼
                 correção e feedback operacional
```

### 5.1 Planos separados

O programa preserva quatro responsabilidades:

1. **cobertura:** encontrar oportunidades dentro da tese definida;
2. **aquisição:** obter o conjunto documental suficiente e sua versão;
3. **representação:** extrair fatos, relações e chunks com evidência; e
4. **consumo:** responder, ranquear e escrever sem ocultar incerteza.

Uma métrica boa em um plano não compensa falha em outro. Precisão alta da
extração não prova cobertura; cobertura alta não prova fidelidade documental.

## 6. Invariantes globais

As specs filhas devem preservar os invariantes existentes de
`system-coherence.md` e adicionar estes:

1. **Escopo explícito:** cobertura é avaliada contra a tese de relevância, nunca
   contra “todos os editais do Brasil”.
2. **Sem blacklist institucional:** uma oportunidade fora do escopo não elimina
   o domínio, órgão ou fonte de futuras buscas.
3. **Proveniência ou desconhecido:** fato crítico publicado possui evidência
   rastreável ou estado explícito de ausência, inferência ou conflito.
4. **Inferência não vira citação:** conteúdo inferido nunca é apresentado como
   declaração do documento.
5. **Trecho verificável:** evidência textual declarada deve ser localizada no
   artefato-fonte versionado correspondente.
6. **Retificação rastreável:** regra mais nova não sobrescreve silenciosamente a
   anterior; a precedência e a derivação permanecem auditáveis.
7. **Falha parcial visível:** prontidão de catálogo, matching e RAG permanece
   independente; sucesso de uma superfície não mascara falha de outra.
8. **Abstenção segura:** ausência de evidência em campo crítico não autoriza
   preencher por plausibilidade nem eliminar empresa por elegibilidade.
9. **Qualidade antes de promoção automática:** conflito ou baixa confiança em
   fatos críticos conduz a revisão ou quarentena conforme contrato versionado.
10. **Mudança medida:** alteração de coletor, parser, prompt, modelo, schema,
    normalização ou precedência executa os gates afetados antes de promoção.
11. **Fonte recuperável:** uma citação continua verificável após redeploy e não
    depende apenas de filesystem efêmero ou URL ainda disponível.
12. **Sem promessa absoluta:** interfaces e documentação distinguem fontes
    monitoradas, descoberta aberta, data de verificação e limitações conhecidas.
13. **Autoridade preservada por origem:** FINEP, FAPESP, FAPESC, Web, EMBRAPII e
    catálogos curados mantêm seus produtores canônicos até spec explícita em
    contrário.
14. **Extensão aditiva:** provenance nasce por migration aditiva e dual-write;
    consumidores atuais não dependem dela antes de equivalência comprovada.
15. **Equivalência do gold:** para a mesma entrada, a projeção canônica anterior
    deve permanecer equivalente quando se removem apenas os campos novos de
    proveniência e metadados operacionais esperados.
16. **Sem runtime de spike:** experimento pode reduzir incerteza técnica, mas
    nunca vira pipeline paralelo, fonte de verdade ou dependência produtiva.
17. **Curadoria não presume qualidade:** rótulo “curado”, origem oficial ou
    coleta ampla não substituem validação de relevância, completude, vigência e
    evidência conforme o tipo de entidade.
18. **Sem RAG artificial:** ausência de corpus suficiente para ICT ou investidor
    permanece explícita; o sistema não cria chunks sintéticos para aparentar
    cobertura factual.

## 7. Modelo de confiança

Confiança é propriedade do caminho do dado, não uma nota subjetiva produzida
pela mesma LLM que extraiu o valor.

Cada fato relevante deve permitir responder:

- qual é o valor normalizado;
- qual é seu estado factual;
- qual documento, página/bloco e trecho o sustentam;
- quando o documento foi coletado e qual hash identifica a versão;
- qual produtor e versão geraram a representação;
- quais validadores passaram ou falharam; e
- se houve revisão humana ou conflito entre fontes.

No primeiro ciclo, estados categóricos e resultados de validação prevalecem
sobre scores numéricos de confiança. Uma spec filha só pode introduzir score
numérico se houver calibração e interpretação operacional documentadas.

## 8. Métricas do programa

### 8.1 Cobertura e operação

- fontes prioritárias monitoradas e saudáveis;
- tempo entre publicação conhecida, descoberta e publicação no Radar;
- recall retrospectivo sobre oportunidades relevantes curadas;
- rendimento e taxa de aprovação por fonte, query e mecanismo;
- distribuição regional, por mecanismo e por público elegível;
- documentos aguardando revisão, desatualizados ou com falha; e
- novas fontes úteis aprendidas pela descoberta aberta.

Essas métricas são proxies declarados. Nenhuma delas, isoladamente, autoriza
afirmar cobertura total do ecossistema.

### 8.2 Representação e confiança

- acurácia de presença/abstenção e valor por campo;
- faithfulness e localização das evidências;
- recall estrutural de headings, tabelas e seções;
- precisão de relações do KG;
- percentual de fatos críticos com evidência válida;
- taxa de conflito, quarentena e correção humana; e
- regressões por fonte, formato e dificuldade documental.

### 8.3 Resultado de produto

- respostas factuais do Explorar com suporte suficiente;
- falsos negativos e falsos positivos de elegibilidade;
- recall e qualidade do matching sobre catálogo confiável;
- recuperação de trechos corretos para escrita; e
- capacidade do usuário de inspecionar origem, vigência e incerteza.

## 9. Mapa das specs filhas

| Ordem | Spec | Resultado | Dependências | Estado |
|---:|---|---|---|---|
| 00 | [`radar-data-trust-00-relevance-contract.md`](radar-data-trust-00-relevance-contract.md) | relevância de oportunidades e atores, com critérios próprios por `kind` | esta spec-mãe | vigente (RT00-T01 a T07 concluídas) |
| 01 | [`radar-data-trust-01-provenance.md`](radar-data-trust-01-provenance.md) | evidência estruturada do documento ao consumo | 00 | vigente (RT01-T01 a RT01-T13 concluídas e auditadas) |
| 02 | `radar-data-trust-02-quality-gates.md` | goldens representativos, gates por camada e E2E | 00; contrato de 01 | vigente; auditoria Codex aprovada |
| 03 | [`radar-data-trust-03-source-coverage.md`](radar-data-trust-03-source-coverage.md) | descoberta aberta multicanal, saúde, atribuição e métricas de cobertura | 00 e sinais de 02 | vigente (RT03-T01 a T07 concluídas e auditadas) |
| 04 | [`radar-data-trust-04-source-bundles.md`](radar-data-trust-04-source-bundles.md) | documentos de oportunidades, retificações e páginas oficiais de atores versionados | 01; métricas de 02 | vigente (RT04-T01 a T07 concluídas e auditadas; merge `285a89746`) |
| 05 | [`radar-data-trust-05-exception-review.md`](radar-data-trust-05-exception-review.md) | revisão humana por conflito/baixa confiança e feedback | 01, 02 e 04 | vigente (RT05-T01 a T09 concluídas; reconciliação local fechada em 2026-07-29) |
| 06 | `radar-data-trust-06-adaptive-extraction.md` | cascata texto/layout/OCR/visão dirigida por falhas medidas | 02 e 04; sinais de 05 | planejada |

Specs futuras só entram nesta tabela após problema comprovado. A numeração não
deve ser reutilizada para outra responsabilidade.

## 10. Ordem e paralelismo

- **00 precede todas:** sem escopo, recall e cobertura não têm denominador.
- **01 precede confiança visível:** gates podem começar a curar dados em
  paralelo, mas o contrato de evidência deve existir antes de persistência nova.
- **02 e 03 podem avançar em paralelo:** qualidade de representação e cobertura
  têm métricas independentes.
- **04 precede revisão de conflito documental:** não se resolve precedência sem
  identificar versões e papéis dos documentos.
- **06 é deliberadamente tardia:** OCR e visão são escolhidos a partir de falhas
  observadas, não como substituição especulativa dos adapters existentes.

## 11. Governança SDD

### 11.1 Contrato de cada spec filha

Cada filha deve declarar:

- problema comprovado e evidência no runtime;
- resultado e não objetivos;
- comportamento atual e pretendido;
- invariantes herdados e novos;
- modelo de dados e contratos de API quando aplicável;
- compatibilidade, backfill, rollout e rollback;
- observabilidade e tratamento de falhas;
- plano de testes, evals e gates;
- tasks ordenadas, com arquivos e dependências; e
- critérios objetivos de conclusão.

### 11.2 Implementação delegada

Uma task entregue a um implementador externo deve ser autocontida e não pode
reinterpretar a spec. O implementador entrega código, testes, migrations e
evidências do gate em commits delimitados. Divergência entre spec e realidade
invalida a task afetada e volta à revisão documental antes de alterar o contrato.

Planos e relatórios ativos desta família vivem exclusivamente em
[`docs/execution/radar-data-trust/`](../execution/radar-data-trust/). Eles não
competem com a autoridade normativa das specs.

### 11.3 Auditoria independente

A auditoria de cada task verifica, no mínimo:

- aderência semântica, não apenas presença de arquivos;
- caminhos de consumo por import, SQL, registry, dispatch e strings;
- migração e compatibilidade com dados existentes;
- falha parcial, idempotência e retry;
- ausência de vazamento de conteúdo ou credenciais;
- cobertura de testes e evals proporcionais; e
- reconciliação de documentação e runtime.

### 11.4 Uso restrito de spikes

Spikes são permitidos somente para incertezas técnicas delimitadas, como
resolução ambígua de trecho, comparação de OCR/layout/visão ou custo de
backfill. Devem declarar hipótese, corpus, métrica e condição de encerramento.

- código produtivo não importa módulos de `spikes/`;
- spike não cria tabela ou artefato autoritativo consumido em produção;
- resultado termina em decisão registrada, teste, spec revisada ou código
  reimplementado no pacote produtivo; e
- material descartável não permanece como segunda implementação mantida.

### 11.5 Proporcionalidade pré-beta

O programa está em pré-beta. Specs, planos e relatórios devem ser simples e
diretos; a validação protege contratos e dependências reais sem tentar cobrir
todas as combinações possíveis.

- cada task roda testes direcionados e lint apenas no escopo alterado;
- cada origem usa uma fixture representativa no primeiro ciclo;
- a suíte completa roda no fechamento de cada spec filha, não após toda task;
- eval externa roda somente quando prompt, modelo ou comportamento de IA mudar;
- migrations, RLS, isolamento, idempotência e risco de perda de dados nunca são
  dispensados por estarem em pré-beta; e
- dúvida de produto interrompe a task e volta ao proprietário, sem decisão
  implícita do implementador.

## 12. Estratégia de rollout

1. Mudanças de schema são aditivas e nullable/default-safe antes do backfill.
2. Produtores passam a gravar o contrato novo antes de consumidores exigirem-no.
3. Cada origem comprova equivalência do gold em fixtures próprias antes de
   habilitar leitura nova.
4. APIs expõem campos novos de forma compatível durante a migração.
5. Métricas rodam em shadow antes de bloquear promoção.
6. Gates só se tornam oficiais com corpus, threshold e proprietário aceitos.
7. Backfills registram versão do produtor e não fabricam coordenadas ausentes.
8. Remoção de campos legados exige telemetria ou busca de consumidores e spec
   reconciliada.

## 13. Não objetivos do programa

- construir um cadastro exaustivo de órgãos brasileiros;
- prometer que nenhuma oportunidade relevante será perdida;
- substituir interpretação jurídica ou confirmação na fonte oficial;
- transformar toda informação do ecossistema em nó ou relação do KG;
- reintroduzir o hipergrafo legado ou criar um banco de grafo por princípio;
- substituir adapters dedicados por um extrator universal sem evidência;
- aplicar OCR, visão ou múltiplos modelos a todos os documentos; e
- resolver qualidade downstream mascarando falhas de aquisição ou extração.

## 14. Critérios globais de conclusão

O programa alcança seu estado-alvo quando:

1. o escopo de oportunidades é classificável e coberto por golden;
2. todo fato crítico publicado possui evidência verificável ou estado explícito
   que impeça falsa certeza;
3. relações relevantes do KG e chunks exibidos ao usuário são rastreáveis;
4. retificações e conflitos documentais possuem precedência auditável;
5. triagem, extração, estruturação e consumo têm gates representativos;
6. cobertura e frescor das fontes monitoradas são observáveis sem alegação de
   exaustividade;
7. revisão humana concentra-se em exceções identificadas pelo sistema; e
8. Explorar, Radar e Escrita comunicam origem, vigência e incerteza de modo
   coerente.

## 15. Autoridade e reconciliação

- Esta spec governa o **programa pretendido** e as relações entre suas filhas.
- `docs/domain/schema.md` e `docs/domain/sources/` continuam autoritativos para
  regras consumidas pelo código.
- `docs/architecture.md` descreve somente o runtime implementado.
- As specs existentes de
  [`discovery-operations.md`](discovery-operations.md),
  [`durable-source-docs.md`](durable-source-docs.md),
  [`v3-unified.md`](v3-unified.md),
  [`data-plane-convergence.md`](data-plane-convergence.md) e
  [`evaluation-operations.md`](evaluation-operations.md) permanecem válidas até
  uma filha declarar alteração explícita.
- Ao concluir uma filha, atualizar seu status, esta tabela e as fontes
  autoritativas afetadas; não copiar o estado implementado para múltiplos docs.
