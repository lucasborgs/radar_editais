# Radar Data Trust 03 — Cobertura da Descoberta e saúde dos canais

**Status:** proposta revisada para aprovação · **Data:** 2026-07-26
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)
**Contratos anteriores:** [`radar-data-trust-00-relevance-contract.md`](radar-data-trust-00-relevance-contract.md), [`radar-data-trust-02-quality-gates.md`](radar-data-trust-02-quality-gates.md)
**Ordem:** 03 · **Impacto:** alto e incremental; aquisição e operação da Descoberta

---

## 1. Problema comprovado

O Radar precisa encontrar oportunidades relevantes para startups e PMEs de
base tecnológica mesmo quando não conhece previamente a organização que as
publicará.

Hoje há dois caminhos complementares:

- fontes dedicadas e Web curada, com coleta determinística; e
- Descoberta aberta por Tavily, DOU e expansão opcional de hubs.

Esse desenho é correto, mas ainda não é governável:

- sucessos de coleta não possuem histórico por canal;
- várias falhas são absorvidas e ficam visíveis apenas em logs;
- Tavily, DOU e filhos de hubs perdem sua identidade antes do staging;
- queries são strings sem identidade estável nem rendimento posterior;
- a decisão humana não retroalimenta a estratégia de busca;
- não sabemos quais canais e famílias de query encontram material útil; e
- um domínio novo que produz oportunidades relevantes não é reconhecido como
  candidato a monitoramento recorrente.

O problema não é a ausência de um cadastro completo de órgãos brasileiros. É a
ausência de instrumentos para saber se a combinação de canais está encontrando
oportunidades relevantes na cauda longa desconhecida.

## 2. Resultado

Entregar uma Descoberta aberta multicanal, observável e adaptável:

1. declarar os canais de aquisição existentes e suas responsabilidades;
2. registrar saúde, frescor e resultados de cada execução;
3. atribuir candidatos a canal e família de busca;
4. medir o funil até a decisão editorial;
5. revelar lacunas e domínios emergentes;
6. permitir que queries sejam ajustadas com evidência; e
7. mostrar ao operador o que está funcionando e quais limites permanecem.

Esta spec não promete encontrar toda oportunidade publicada. Ela maximiza o
recall dentro da tese do produto e torna pontos cegos detectáveis.

## 3. Tese de cobertura

O Radar usa duas camadas:

```text
FONTES CONHECIDAS
FINEP, FAPESP, FAPESC e URLs Web curadas
→ coleta determinística e alta precisão

DESCOBERTA ABERTA
busca web + DOU + expansão de hubs
→ recall sobre organizações e oportunidades não cadastradas

AMBAS
→ triagem de relevância → staging → revisão → pipeline canônico
```

Fontes conhecidas são âncoras, não o limite do universo pesquisado. A
Descoberta aberta existe justamente para alcançar a cauda longa sem exigir um
scraper dedicado para cada órgão, empresa ou programa.

Quando um domínio desconhecido produz oportunidades aprovadas repetidamente,
ele vira candidato a monitoramento dedicado. Isso melhora eficiência e
confiabilidade, mas não é pré-requisito para que suas oportunidades sejam
encontradas.

## 4. Princípios

1. **Oportunidade primeiro:** cobertura mede oportunidades relevantes
   encontradas, não quantidade de instituições cadastradas.
2. **Multicanal:** busca aberta não depende conceitualmente de uma única lista
   de fontes nem confunde Tavily com garantia de completude.
3. **Âncoras + cauda longa:** scrapers dedicados cobrem fontes previsíveis; a
   descoberta aberta procura o que não foi antecipado.
4. **Feedback editorial:** aprovação e rejeição humanas medem rendimento dos
   canais e das famílias de query.
5. **Zero é ambíguo:** ausência de resultados não prova ausência de
   oportunidades.
6. **Operação não prova conteúdo:** execução concluída não garante que o portal
   publicou tudo nem que o motor de busca indexou tudo.
7. **Sem score mágico:** estados e contadores observados prevalecem sobre nota
   subjetiva de “cobertura”.
8. **Sem blacklist institucional:** resultado irrelevante não elimina o domínio
   ou organização de buscas futuras.

## 5. Canais iniciais

O registro contém somente canais de aquisição de oportunidades já existentes:

| Canal | Tipo | Papel |
|---|---|---|
| `finep` | `dedicated` | coleta determinística do portal FINEP |
| `fapesp` | `dedicated` | coleta determinística do portal FAPESP |
| `fapesc` | `dedicated` | coleta determinística do portal FAPESC |
| `web_curated` | `curated_web` | URLs aprovadas em `web_sources` |
| `open_search` | `open_search` | busca ampla pelo port existente, hoje Tavily |
| `dou` | `official_feed` | oportunidades publicadas no DOU |
| `hub_expansion` | `hub` | desafios-filho encontrados em hubs |

`open_search` é o canal lógico. Tavily é o backend atual de
`radar.core.web_search`, não uma decisão permanente do domínio. Trocar ou
adicionar provider no futuro não muda staging, triagem ou métricas públicas.

Investidores, ICTs e programas versionados não entram neste registro: são
catálogos de atores, não canais de aquisição de oportunidades. Uma chamada
publicada por um investidor, empresa ou ICT continua sendo encontrada pela
Descoberta como oportunidade.

### 5.1 Autoridade

As definições vivem em bloco YAML versionado em
`docs/domain/sources/_coverage.md`, lido pelo loader existente
`radar.core.kg.schema`. Código não mantém lista normativa paralela.

Cada canal declara:

```yaml
source_key: open_search
display_name: Busca aberta
mode: open_search
scope_note: Oportunidades não cobertas pelas fontes dedicadas e pelo DOU
expected_interval_hours: 24
enabled_by_default: true
```

Canal gated por flag registra o nome da flag, nunca seu valor ou segredo.

## 6. Famílias de busca

As queries de `_discovery.md` passam a ter identificadores estáveis e uma
finalidade de negócio:

| Família inicial | O que procura |
|---|---|
| `state_innovation_funding` | chamadas estaduais e FAPs fora das fontes dedicadas |
| `corporate_open_innovation` | desafios e pilotos publicados por empresas/hubs |
| `startup_acceleration` | aceleração, incubação e programas com benefício concreto |
| `international_brazil_access` | oportunidades internacionais acessíveis a empresas brasileiras |

O texto da query continua configurável em documentação. Métricas persistem
somente o identificador da família, não a query completa.

Uma família pode possuir várias queries. Adicionar ou ajustar query exige
registrar o motivo e comparar seu rendimento, mas não exige criar uma nova
fonte canônica.

## 7. Contrato operacional

### 7.1 `source_runs`

Uma tabela aditiva registra uma linha por canal observado em uma execução:

| Campo | Contrato |
|---|---|
| `id` | UUID |
| `batch_id` | UUID compartilhado pela rodada |
| `source_key` | chave do canal |
| `mode` | modalidade congelada |
| `status` | `running`, `succeeded`, `partial`, `failed` ou `skipped` |
| `started_at`, `completed_at` | timestamps UTC |
| `records_observed` | itens retornados, nullable |
| `records_emitted` | itens que atravessaram a etapa, nullable |
| `records_staged` | itens enviados ao staging, nullable |
| `error_count` | falhas observadas |
| `reason_code` | razão canônica curta |
| `metrics` | contadores adicionais não sensíveis |

RLS fica habilitada sem policy de usuário final. Worker escreve via service
role; API administrativa lê após `AdminUserId`.

Não persistir traceback, credencial, prompt, resposta LLM, corpo documental,
query completa ou URL com parâmetros sensíveis.

### 7.2 Atribuição no staging

`discovered_opportunities` recebe campos aditivos e nullable:

| Campo | Função |
|---|---|
| `discovery_run_id` | liga o candidato à rodada |
| `discovery_channel` | `open_search`, `dou` ou `hub_expansion` |
| `query_family` | família estável; nullable para DOU/hub sem query |
| `origin_domain` | hostname normalizado e sem query/path |

Dados legados permanecem válidos com todos os campos `null`.

Esses campos não alteram relevância, status editorial, dedup, promoção ou
materialização. Servem apenas para atribuição e aprendizado operacional.

## 8. Saúde e frescor

A API deriva estados; não grava uma segunda verdade:

| Estado | Condição |
|---|---|
| `disabled` | canal gated explicitamente desligado |
| `unknown` | nunca observado ou resultado ambíguo |
| `healthy` | execução comprovadamente concluída dentro da janela |
| `degraded` | execução parcial ou com falhas absorvidas |
| `failing` | última execução falhou |
| `stale` | duas janelas esperadas sem conclusão saudável |

Precedência: `disabled` → `failing` → `degraded` → `stale` → `healthy` →
`unknown`.

“Healthy” significa que o caminho técnico concluiu; não significa cobertura
completa da internet. Resultado vazio sem prova suficiente permanece
`unknown`.

## 9. Instrumentação

### 9.1 Fontes dedicadas e Web curada

O loop de `run_daily_etl` abre e finaliza um run por scraper sem alterar
payload, retry, alertas, bronze, silver ou gold.

Registra contagem observada e falha do scraper. Não infere falha parcial quando
o produtor não a expõe.

### 9.2 Descoberta aberta

A rodada preserva a origem interna dos candidatos e mede por canal/família:

- candidatos retornados;
- candidatos deduplicados;
- falhas por query;
- triagens executadas e puladas por cache;
- rejeições da triagem;
- falhas de triagem e extração;
- filhos de hubs encontrados;
- registros produzidos; e
- registros enviados ao staging.

O retorno público `discover_opportunities(...)->list[dict]` permanece
compatível.

Ausência de credencial é `skipped`. DOU em fim de semana pode ser `skipped`.
Retorno vazio em condição indistinguível permanece ambíguo.

### 9.3 Feedback da revisão

As decisões existentes em `discovered_opportunities` permitem calcular:

- taxa de aprovação por canal;
- taxa de aprovação por família de query;
- pendências e rejeições;
- tempo entre descoberta e revisão; e
- domínios que originaram oportunidades aprovadas repetidamente.

Domínio recorrente aparece como **candidato** a monitoramento dedicado. A spec
não o cadastra automaticamente, não cria scraper e não promove conteúdo sem
operador.

## 10. Métricas de cobertura

O painel apresenta:

- canais ativos, observados, degradados e atrasados;
- última tentativa e último sucesso;
- volume e rendimento por canal;
- rendimento por família de query;
- distribuição dos aprovados por tipo de oportunidade e origem disponível;
- domínios emergentes com aprovações; e
- limitações e denominadores.

Métrica sem denominador retorna `null`, nunca zero fabricado.

### 10.1 Recall

Recall absoluto da web é impossível de provar. O Radar usa três sinais:

1. rendimento prospectivo dos canais e queries;
2. oportunidades relevantes conhecidas que foram ou não encontradas; e
3. diversidade dos aprovados por mecanismo, região e tipo.

Esta versão prepara a atribuição e o baseline. Um corpus retrospectivo só pode
virar gate depois de curadoria representativa; os goldens de relevância não
servem como denominador de descoberta.

## 11. API e experiência do operador

Adicionar `GET /source-coverage`, protegido por `AdminUserId`, e painel
recolhido no topo de `/discovered`.

Texto canônico:

> Fontes e canais monitorados pelo Radar

O painel mostra saúde, rendimento, famílias de busca e domínios emergentes.
Não oferece edição de registry, query, flag, retry, crawler ou promoção
automática.

Falha do painel não bloqueia listar, promover ou rejeitar oportunidades.

## 12. Compatibilidade e rollout

1. Migration aditiva: cria `source_runs` e acrescenta colunas nullable ao
   staging.
2. Aplicar migration antes do runtime instrumentado.
3. Telemetria é best-effort e nunca bloqueia aquisição.
4. API tolera tabela vazia e registros legados.
5. UI tolera API indisponível.
6. Não existe backfill fictício de runs ou atribuição legada.
7. Rollback remove leitores/escritores; schema pode permanecer.
8. Nenhum prompt, modelo, classificador, ranking, KG ou RAG muda.

## 13. Validação proporcional

Cobertura mínima:

- registry e famílias de query válidos;
- persistência/idempotência de runs;
- derivação conservadora de saúde;
- ETL: sucesso, falha, vazio ambíguo e telemetria indisponível;
- Descoberta: atribuição Tavily/DOU/hub e dedup entre canais;
- funil editorial por canal/família;
- domínio emergente sem promoção automática;
- API administrativa, tabela vazia e sanitização;
- frontend e fallback.

Uma fixture por canal relevante basta. Não criar threshold ou gate de recall
sem corpus aprovado.

Por task: testes direcionados, Ruff e `git diff --check`. Suíte completa,
TypeScript e lint somente no fechamento.

## 14. Tasks propostas

| Task | Resultado |
|---|---|
| `RT03-T01` | contrato de canais e famílias de busca |
| `RT03-T02` | migration, `source_runs` e atribuição nullable no staging |
| `RT03-T03` | saúde das fontes dedicadas/Web curada |
| `RT03-T04` | instrumentação multicanal da Descoberta |
| `RT03-T05` | métricas do funil, lacunas e domínios emergentes |
| `RT03-T06` | API administrativa e painel |
| `RT03-T07` | baseline, reconciliação e fechamento |

T01 precede T02. T03 e T04 podem avançar em paralelo após T02, com pouso
serial em `tasks.py` se necessário. T05 depende de T04. T06 depende de
T03–T05. T07 fecha tudo.

## 15. Não objetivos

- cadastrar todos os órgãos, empresas ou fontes do Brasil;
- depender de lista completa de instituições para buscar oportunidades;
- garantir matematicamente completude da web;
- descobrir ou enriquecer o catálogo de investidores/ICTs como atores;
- criar scraper dedicado automaticamente;
- trocar provider de busca sem evidência;
- criar alertas, pager ou plataforma genérica de observabilidade;
- alterar relevância, promoção, extração, gold, matching ou RAG; ou
- remover oportunidade/fonte automaticamente por baixo rendimento.

## 16. Critérios de conclusão

1. canais conhecidos e abertos são observáveis separadamente;
2. todo novo candidato possui atribuição quando tecnicamente disponível;
3. decisões editoriais retroalimentam métricas de canal e query;
4. domínios emergentes são visíveis sem promoção automática;
5. zero e falha absorvida não aparecem como sucesso enganoso;
6. operador entende saúde, rendimento e limitações;
7. nenhuma implementação acessa produção/rede durante testes;
8. suíte completa e frontend permanecem no baseline comparativo; e
9. a documentação não confunde cobertura medida com promessa de exaustividade.
