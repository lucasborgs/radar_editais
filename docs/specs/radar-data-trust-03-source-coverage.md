# Radar Data Trust 03 — Cobertura e saúde das fontes

**Status:** proposta para aprovação · **Data:** 2026-07-26  
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)  
**Contratos anteriores:** [`radar-data-trust-00-relevance-contract.md`](radar-data-trust-00-relevance-contract.md), [`radar-data-trust-02-quality-gates.md`](radar-data-trust-02-quality-gates.md)  
**Ordem:** 03 · **Impacto:** médio; ingestão, Descoberta e operação

---

## 1. Problema comprovado

O Radar já possui quatro scrapers registrados (`finep`, `fapesp`, `fapesc` e
`web`), Descoberta por Tavily/DOU, catálogos versionados de atores e
`pipeline_errors`. Entretanto, o sistema não consegue responder de forma
durável:

- quais canais deveriam estar ativos;
- quando cada canal foi tentado e quando funcionou pela última vez;
- quantos registros foram observados e persistidos;
- se uma fonte está atrasada, falhando ou nunca foi observada;
- qual é o rendimento da Descoberta até o gate editorial; ou
- quão recentes são os catálogos versionados.

`run_daily_etl` persiste falhas, mas não sucessos. Vários coletores toleram
falhas por item e retornam uma lista parcial ou vazia; por isso, ausência de
exceção não prova completude. A Descoberta também captura falhas por
query/candidato e retorna apenas os registros finais. Hoje, “zero resultados”
pode significar nenhuma novidade, fonte indisponível, credencial ausente ou
falha absorvida.

Sem esse control plane, cobertura não é mensurável e fontes silenciosamente
degradadas parecem saudáveis.

## 2. Resultado

Entregar uma visão operacional mínima e auditável das fontes que o Radar
deliberadamente monitora:

1. registro autoritativo dos canais e de sua modalidade;
2. histórico aditivo de execuções e resultados por canal;
3. estado derivado de saúde e frescor, com semântica conservadora;
4. métricas de rendimento da Descoberta e dos catálogos versionados;
5. API e painel administrativo somente leitura; e
6. baseline local reproduzível, sem alegação de cobertura exaustiva.

Esta spec mede o sistema existente. Ela não adiciona fontes, não muda o escopo
de relevância e não promove automaticamente oportunidades.

## 3. Princípios de produto

1. **Cobertura declarada, não universal:** o denominador é o conjunto de canais
   registrados como monitorados, nunca “todos os órgãos do Brasil”.
2. **Canal não é oportunidade:** uma instituição pode publicar itens dentro e
   fora do escopo; a relevância continua sendo decidida por oportunidade.
3. **Operação não prova conteúdo:** uma coleta concluída prova que o caminho
   técnico terminou, não que o portal publicou tudo nem que os dados estão
   corretos.
4. **Zero é ambíguo:** resultado vazio nunca é convertido automaticamente em
   “saudável” quando o produtor não consegue distinguir ausência real de falha.
5. **Frescor depende da modalidade:** cron diário, descoberta aberta e catálogo
   versionado não compartilham a mesma régua.
6. **Sem score mágico:** estados categóricos e contadores observados substituem
   uma nota numérica subjetiva de cobertura.

## 4. Estado atual reutilizável

| Capacidade | Reuso | Limitação atual |
|---|---|---|
| `SCRAPER_REGISTRY` | identidade dos scrapers ativos | não declara finalidade, cadência nem saúde |
| `run_daily_etl` | ponto único de orquestração dos scrapers | só agrega logs e erros |
| `pipeline_errors` | detalhe técnico de falhas | não registra sucesso nem agrupa uma rodada |
| Descoberta Tavily/DOU | aquisição aberta | métricas intermediárias ficam apenas em logs |
| `discovered_opportunities` | resultados e decisões editoriais | não guarda uma execução nem todos os descartes |
| `web_sources` | URLs curadas ativas | não guarda última tentativa ou sucesso |
| catálogos versionados | investidores, programas e ICTs | frescor não é observado uniformemente |
| página `/discovered` | console administrativo existente | não mostra saúde das fontes |

Nenhuma tabela nova substitui `pipeline_errors`, `web_sources`,
`discovered_opportunities` ou os artefatos versionados.

## 5. Tipos de canal

O registro distingue a modalidade porque “frescor” significa coisas
diferentes:

| Modalidade | Canais iniciais | Sinal observado |
|---|---|---|
| `scheduled_scraper` | FINEP, FAPESP, FAPESC, Web curada | execução diária e registros retornados |
| `discovery` | Tavily, DOU | candidatos, triagens, aceites e staging |
| `versioned_catalog` | investidores, programas, ICTs EMBRAPII | hash do artefato, registros e datas declaradas de verificação |

O conjunto inicial reflete somente produtores que já existem. Adicionar fonte
futura exige documentação de domínio e entrada explícita no registro; descobrir
uma URL nova não cria automaticamente um novo canal monitorado.

### 5.1 Autoridade do registro

As definições vivem em bloco YAML versionado em
`docs/domain/sources/_coverage.md` e são lidas pelo loader existente
`radar.core.kg.schema`. Código não mantém uma segunda lista normativa.

Cada definição contém:

```yaml
source_key: finep
display_name: FINEP
mode: scheduled_scraper
scope_note: Chamadas empresariais publicadas no portal FINEP
expected_interval_hours: 24
enabled_by_default: true
```

Regras:

- `source_key` é estável e lowercase;
- `expected_interval_hours` só existe para canal realmente periódico;
- canal gated por flag informa a flag, sem copiar seu valor para o banco;
- catálogos versionados não recebem SLA fictício;
- segredo, query completa e URL com parâmetros sensíveis não entram no registro.

## 6. Contrato operacional

### 6.1 Uma tabela aditiva

`source_runs` registra uma linha por canal observado em uma execução:

| Campo | Contrato |
|---|---|
| `id` | UUID |
| `batch_id` | UUID compartilhado pela rodada do cron |
| `source_key` | chave do registro |
| `mode` | modalidade congelada na execução |
| `status` | `running`, `succeeded`, `partial`, `failed` ou `skipped` |
| `started_at`, `completed_at` | timestamps UTC |
| `records_observed` | itens retornados pelo produtor, nullable |
| `records_emitted` | itens que atravessaram a etapa, nullable |
| `records_staged` | itens enviados ao staging, nullable |
| `error_count` | falhas observadas, default zero |
| `reason_code` | motivo canônico curto para `partial`, `failed` ou `skipped` |
| `metrics` | contadores adicionais não sensíveis, JSON |

`source_runs` é global, RLS habilitada e sem policy para usuários finais.
Worker escreve com service role; a API administrativa lê com service role após
o gate `AdminUserId`.

Não persistir traceback, chave, corpo de documento, prompt, resposta LLM ou URL
com query string. Detalhes técnicos continuam em logs e `pipeline_errors`.

### 6.2 Estados públicos derivados

A API deriva, sem gravar uma segunda verdade:

| Estado | Condição |
|---|---|
| `disabled` | canal condicionado a flag explicitamente desligada |
| `unknown` | nunca observado ou resultado vazio ambíguo sem sucesso comprovável |
| `healthy` | última execução comprovadamente concluída dentro da janela esperada |
| `degraded` | última execução parcial ou com falhas absorvidas |
| `failing` | última execução falhou |
| `stale` | canal periódico passou duas janelas esperadas sem conclusão saudável |

Precedência: `disabled` → `failing` → `degraded` → `stale` → `healthy` →
`unknown`.

Para os crons diários, `stale` significa duas janelas perdidas
(`2 × expected_interval_hours`). Isso é detector operacional, não garantia de
que uma oportunidade foi publicada ou encontrada.

Catálogos versionados exibem `last_artifact_observed_at`, hash, quantidade de
registros e completude de `verificado_em`; não recebem artificialmente
`healthy/stale` sem política de revisão aprovada.

## 7. Instrumentação

### 7.1 ETL diário

Para cada item de `SCRAPER_REGISTRY`, o orquestrador:

1. abre `source_run`;
2. executa o scraper sem alterar seu payload;
3. registra `records_observed`;
4. finaliza como `succeeded` ou `failed`; e
5. mantém `pipeline_errors` como detalhe da falha.

Quando o scraper expuser falhas parciais de forma confiável, registra
`partial`. A primeira versão não infere falha parcial a partir de silêncio.
Persistência da observabilidade é best-effort e nunca derruba a ingestão.

### 7.2 Descoberta

Tavily e DOU são canais distintos. A execução mede, quando observável:

- candidatos retornados;
- falhas por query;
- candidatos deduplicados;
- triagens executadas e puladas por cache;
- rejeições;
- falhas de triagem/extração;
- registros produzidos; e
- registros enviados ao staging.

A função pública existente continua retornando `list[dict]`. A instrumentação
deve usar um relatório interno/aditivo e não quebrar scripts ou testes atuais.
Uma rodada sem credencial é `skipped`, não sucesso com zero.

DOU em fim de semana pode ser `skipped` com motivo canônico. Em dia útil,
retorno vazio que não distingue indisponibilidade de edição vazia permanece
`unknown`; não é marcado como saudável.

### 7.3 Catálogos versionados

Uma inspeção determinística e sem rede registra:

- caminho lógico do artefato;
- hash de conteúdo;
- quantidade de registros;
- quantidade com/sem `verificado_em`; e
- maior e menor data declarada, quando válidas.

Isso vale para investidores, programas e o snapshot EMBRAPII consumido pelo
gold. A inspeção não reclassifica, enriquece nem corrige registros.

## 8. Métricas de cobertura

O serviço de leitura agrega:

- canais registrados, habilitados e observados;
- distribuição por estado operacional;
- última tentativa e último sucesso por canal;
- registros observados por execução;
- rendimento da Descoberta: `staged / candidates`, com denominador explícito;
- decisão editorial posterior: promovidos/rejeitados/pendentes por período; e
- completude de verificação dos catálogos versionados.

Métricas sem denominador retornam `null`, nunca zero fabricado.

Recall retrospectivo de oportunidades relevantes fica **fora desta versão**:
não existe ainda corpus representativo de oportunidades conhecidas e perdidas.
Os 14 casos de relevância validam o classificador, não a cobertura das fontes.

## 9. API e experiência do operador

Adicionar `GET /source-coverage`, protegido por `AdminUserId`, com:

- `generated_at`;
- resumo agregado;
- uma linha por canal registrado;
- últimos contadores não sensíveis; e
- limitações explícitas.

O painel entra no topo de `/discovered`, recolhido por padrão, porque essa já é
a superfície administrativa de Descoberta. Exibe estado, última execução,
último sucesso e contadores essenciais. Não cria edição de registry, reexecução
de job, retry ou configuração de flags.

O frontend nunca apresenta “cobertura do Brasil”. Texto canônico:
“Fontes monitoradas pelo Radar”.

## 10. Compatibilidade, rollout e rollback

1. Migration aditiva; nenhuma tabela ou coluna existente muda.
2. Aplicar migration antes do runtime instrumentado.
3. Escrita de telemetria nasce best-effort; falha não bloqueia coleta.
4. API tolera tabela vazia e retorna todos os canais como `unknown/disabled`.
5. UI tolera API indisponível sem bloquear a fila editorial.
6. Não há backfill de execuções fictícias.
7. Rollback remove o runtime leitor/escritor; a tabela pode permanecer sem
   consumidor.
8. Nenhum prompt, modelo, classificador, ranking, KG ou RAG muda.

## 11. Segurança de ambiente

- testes usam `ENVIRONMENT=test`, fixtures e banco fake/local;
- nunca carregar `.env` de produção;
- nenhuma execução externa é necessária para concluir a spec;
- não consultar Supabase Cloud, Tavily, DOU ou LLM durante implementação;
- erros expostos pela API são categóricos e sanitizados; e
- migration remota, deploy e backfill exigem autorização separada.

## 12. Validação proporcional

Cobertura mínima:

- parse e invariantes do registro YAML;
- persistência/idempotência de início e término de uma execução;
- derivação de cada estado público e precedência;
- ETL: sucesso, falha e falha da própria telemetria;
- Descoberta: Tavily/DOU separados, credencial ausente e zero ambíguo;
- catálogos: hash, contagem e datas ausentes;
- API: auth administrativa, tabela vazia e sanitização;
- frontend: TypeScript/lint e estados de fallback.

Uma fixture por modalidade basta. Não criar nova suíte de eval: esta spec mede
operação determinística, não qualidade de modelo.

Por task: testes direcionados, Ruff e `git diff --check`. Suíte completa,
TypeScript e lint somente no fechamento.

## 13. Tasks propostas

| Task | Resultado |
|---|---|
| `RT03-T01` | contrato de domínio + registry autoritativo |
| `RT03-T02` | migration e persistência de `source_runs` |
| `RT03-T03` | instrumentação do ETL diário |
| `RT03-T04` | snapshot dos catálogos versionados |
| `RT03-T05` | métricas da Descoberta Tavily/DOU |
| `RT03-T06` | agregação, API administrativa e painel |
| `RT03-T07` | baseline local, reconciliação e fechamento |

T01 e T02 podem ser implementadas em paralelo após aprovação da spec. T03 e
T04 dependem delas. T05 depende de T01/T02, mas não de T03/T04. T06 depende de
T03–T05. T07 fecha tudo.

## 14. Não objetivos

- catalogar todos os órgãos ou fontes do Brasil;
- adicionar novos scrapers, queries ou canais;
- medir recall sem corpus conhecido;
- criar alertas, pager, SLA contratual ou plataforma de observabilidade;
- editar fontes ou disparar jobs pela UI;
- classificar relevância, corrigir extração ou promover oportunidades;
- monitorar páginas individuais de cada ator; ou
- aplicar políticas automáticas de remoção por frescor.

## 15. Critérios de conclusão

1. todos os canais existentes e intencionais estão no registro autoritativo;
2. cron ETL e Descoberta persistem resultados sem mudar seu comportamento;
3. catálogos versionados produzem snapshot determinístico;
4. estados públicos seguem as regras conservadoras desta spec;
5. operador vê fontes monitoradas, última execução e limitações;
6. nenhuma métrica sem denominador vira zero;
7. nenhum teste ou implementação acessa produção/rede;
8. suíte completa e frontend permanecem no baseline comparativo; e
9. documentação autoritativa e relatório final refletem o runtime entregue.

