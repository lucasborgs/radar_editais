# Radar Data Trust 05 — Revisão humana de exceções

**Status:** vigente · concluída em 2026-07-29 · **Data:** 2026-07-29  
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)  
**Contratos anteriores:** [`radar-data-trust-01-provenance.md`](radar-data-trust-01-provenance.md), [`radar-data-trust-02-quality-gates.md`](radar-data-trust-02-quality-gates.md), [`radar-data-trust-04-source-bundles.md`](radar-data-trust-04-source-bundles.md)  
**Ordem:** 05 · **Impacto:** médio; confiança operacional e vigência

---

> **Nota de reconciliação (RT05-T09, 2026-07-29):** a implementação entregou
> detector temporal em shadow, fila `data_quality_exceptions` /
> `data_quality_reviews`, revisão append-only, read model temporal único, API e
> UI administrativas, e comunicação conservadora em Ecossistema, Radar,
> Explorar, Escrita e Aplicações. `continuous` exige evidência explícita;
> `needs_review` e `closed` não entram no Radar ativo.

## 1. Problema comprovado

O Radar já consegue preservar evidência, localizar fatos e versionar os
documentos que sustentam uma oportunidade. Quando a informação é ausente,
incompatível ou insuficiente, porém, ainda falta uma decisão operacional comum:

- o fato pode ser apresentado como certo;
- a oportunidade pode continuar sendo tratada como acionável;
- o caso precisa de revisão humana; ou
- uma nova coleta deve substituir a decisão anterior.

O problema é especialmente material em `deadline` e `status`. Hoje,
`deadline = null` representa tanto fluxo contínuo quanto prazo desconhecido. Se
o status bruto for `ABERTA`, o Stage 0 do match considera a oportunidade viva,
mesmo sem evidência de que ela seja contínua.

O caso de referência é a
[Chamada pública conjunta Finep e Rede Eureka 2024](https://www.finep.gov.br/e/chamada-publica/222684/747009):

- a página foi publicada em 31/01/2024;
- o portal ainda informa inscrição `Aberta`;
- o campo de prazo está vazio;
- o bronze de 15/07/2026 preserva `status = ABERTA` e `prazo = null`; e
- a própria página já contém resultados e período para interposição de
  recursos, sinais incompatíveis com uma chamada ainda recebendo propostas.

Esse registro pode permanecer ativo indefinidamente no catálogo. Não é falha
de OCR: a fonte oficial apresenta dados temporais incompletos e contraditórios.

Outros fatos críticos podem chegar como `conflicting`, `unknown`, sem locator
resolvido ou com validação reprovada. Sem uma fila comum, essas decisões ficam
espalhadas entre código, documentos e correções manuais sem trilha auditável.

## 2. Resultado pretendido

Entregar um mecanismo enxuto de revisão por exceção que:

1. detecte deterministicamente fatos críticos que não podem ganhar aparência
   de certeza;
2. concentre somente esses casos em uma fila administrativa;
3. apresente valor, evidências, versões e motivo da exceção;
4. registre confirmação ou correção humana com autoria e data;
5. reabra a exceção quando uma nova versão material invalidar a decisão;
6. impeça que vigência desconhecida seja tratada como fluxo contínuo; e
7. produza sinais agregados para orientar a extração adaptativa da spec 06.

O objetivo não é revisar manualmente o catálogo inteiro. O caminho normal
continua automático; humanos recebem apenas casos identificados e explicáveis.

## 3. Princípios de simplicidade

1. **Reusar o data plane:** bundles, proveniência, gold e consumidores atuais
   permanecem autoridades; a revisão não cria catálogo paralelo.
2. **Detecção determinística:** exceções derivam de estados, validadores e
   evidências existentes. Não usar LLM para decidir se um caso precisa de
   revisão.
3. **Sem score abstrato:** baixa confiança significa uma condição concreta,
   como conflito, ausência, locator insuficiente ou validação reprovada.
4. **Uma fila:** oportunidades e futuros casos de atores compartilham contrato
   e armazenamento; não criar workflow por fonte ou por campo.
5. **Decisão auditável:** correção humana não altera documento histórico e não
   apaga a saída anterior do produtor.
6. **Sem autoaprendizado:** feedback é diagnóstico versionado; não modifica
   prompt, regra ou modelo automaticamente.
7. **Validação proporcional:** um caso representativo por comportamento
   material, sem matriz cartesiana de fontes e estados.
8. **Bibliotecas existentes:** Pydantic, FastAPI, Supabase/Postgres e o frontend
   atual bastam; nenhum framework de workflow ou anotação é necessário.

## 4. Escopo

### 4.1 Primeira vertical: fatos temporais de oportunidades

Esta é a primeira vertical porque prazo e validade determinam se a oportunidade
pode aparecer como acionável no Radar.

O contrato separa dois eixos:

```text
temporal_mode = fixed | continuous | unknown
validity_state = active | closed | needs_review
```

| Evidência disponível | `temporal_mode` | `validity_state` |
|---|---|---|
| prazo parseável igual ou posterior à data atual | `fixed` | `active` |
| prazo parseável anterior à data atual | `fixed` | `closed` |
| ausência de prazo com evidência oficial explícita de fluxo contínuo | `continuous` | `active` |
| status oficial encerrado, mesmo sem prazo | `unknown` | `closed` |
| status aberto sem prazo e sem evidência explícita de continuidade | `unknown` | `needs_review` |
| prazo e status incompatíveis sem precedência resolvida | conforme os claims | `needs_review` |
| documentos vigentes apresentam prazos incompatíveis | `fixed` ou `unknown` | `needs_review` |

As regras de conflito têm precedência sobre as regras simples de prazo ou
status. Um prazo futuro não vence silenciosamente uma declaração oficial de
encerramento, nem o inverso.

Para `deadline` armazenado apenas como data, o dia do encerramento permanece
ativo até o fim do dia em `America/Sao_Paulo`. Horário exato só pode ser
apresentado quando vier da fonte; não será fabricado pelo sistema.

`continuous` exige evidência textual ou registro estruturado recuperável. A
mera ausência de prazo nunca prova continuidade.

Esta proposta também explicita uma divergência atual: `docs/domain/schema.md`
usa `deadline > hoje`, enquanto `gold.py`, `entity_catalog.py`, `match_v3.py` e
a UX de prazo tratam a data de hoje como ainda ativa. A aprovação desta spec
escolhe `deadline >= hoje`; a implementação deverá reconciliar o schema
autoritativo e manter uma única regra.

### 4.2 Outros fatos críticos

O mesmo mecanismo pode receber exceções já produzidas pelos contratos
anteriores:

- `FactState.CONFLICTING`;
- fato obrigatório `UNKNOWN` ou `ABSENT`;
- validador crítico com resultado `failed`;
- bundle `partial` quando o campo depende do documento ausente; e
- evidência que não resolve para a versão documental declarada.

No primeiro rollout, os campos de oportunidade elegíveis para revisão são:

- `deadline` e `status`;
- elegibilidade e público participante;
- mecanismo ou benefício material; e
- identidade da oportunidade quando houver duplicidade ou conflito.

Prazo/status são obrigatórios na primeira entrega. Os demais entram somente
quando o pipeline já emitir um estado concreto de exceção; esta spec não exige
novos classificadores para preencher a fila.

### 4.3 Atores

O contrato aceita `investor`, `ict`, `program` e `agency`, mas não exige uma
campanha de revisão desses catálogos no pré-beta. Um ator entra na fila somente
quando um produtor existente emitir conflito ou falha concreta em fato
material, como identidade, situação atual ou atividade relevante.

Conteúdo insuficiente continua `unknown`; não gera chunks artificiais nem
obriga revisão humana de todos os campos ausentes.

### 4.4 Relação com relevância e promoção

Relevância, validade e fit permanecem dimensões diferentes:

- a classificação da spec 00 decide se uma oportunidade pertence ao escopo;
- `promote/reject` da Descoberta continua sendo a decisão editorial sobre um
  candidato Web;
- esta spec decide se um fato crítico é confiável para consumo; e
- matching continua decidindo aderência ao perfil.

Promover uma descoberta não confirma automaticamente seu prazo. Uma
oportunidade relevante pode estar encerrada, e uma oportunidade vigente pode
estar fora do escopo.

## 5. Tipos de exceção

Os códigos iniciais são pequenos e orientados a ação:

| Código | Condição |
|---|---|
| `fact_conflict` | valores incompatíveis sem precedência documental confiável |
| `critical_fact_missing` | fato necessário ao consumo está ausente ou desconhecido |
| `validation_failed` | validador crítico existente reprovou |
| `evidence_unresolved` | evidência ou versão declarada não pode ser recuperada |
| `temporal_status_without_basis` | aberto/ativo sem prazo nem evidência de continuidade |
| `temporal_status_conflict` | prazo, status ou documentos temporais se contradizem |

Novos códigos exigem problema observado e atualização desta spec. Mensagens
livres não substituem códigos e não dirigem comportamento do runtime.

## 6. Contrato da fila e da revisão

### 6.1 Exceção

Uma exceção identifica:

- `subject_kind` e `subject_id`;
- `field_path`;
- `issue_code`;
- estado e valor produzidos;
- referências às evidências e ao `bundle_hash` aplicável;
- versão do produtor/validador;
- `input_fingerprint`, derivado das entradas materiais;
- `status`: `open`, `resolved` ou `superseded`; e
- datas de detecção e última observação.

A chave lógica é
`(subject_kind, subject_id, field_path, issue_code, input_fingerprint)`.
Reprocessar a mesma entrada é idempotente. Nova versão material cria outra
exceção e torna a anterior histórica; não reescreve a decisão já tomada.

### 6.2 Decisão humana

Uma revisão append-only registra:

- `review_id`;
- referência à exceção;
- `actor_id` administrativo;
- `decision`: `confirm`, `correct`, `mark_unknown` ou
  `confirm_continuous`;
- valor corrigido, quando aplicável;
- justificativa curta;
- evidências usadas;
- `reviewed_at`; e
- versão do contrato.

Regras:

1. `confirm` aceita o valor produzido e exige que ele seja recuperável.
2. `correct` exige novo valor e evidência documental existente.
3. `confirm_continuous` aplica-se apenas a prazo e exige evidência oficial
   explícita de continuidade.
4. `mark_unknown` reconhece que não há base suficiente; não transforma ausência
   em certeza.
5. Não existe decisão genérica `ignore`. Um falso positivo deve ser resolvido
   com decisão e justificativa auditáveis.
6. A revisão referencia `ReviewInfo` da spec 01; não cria segundo contrato de
   autoria.

Se o operador localizar uma página oficial ainda não versionada, ela deve
entrar pelo produtor/bundle aplicável antes de sustentar a correção. Colar URL
ou texto solto na revisão não cria evidência autoritativa.

### 6.3 Projeção corrente

A decisão humana cria uma projeção de `FactProvenance` com:

- `producer.kind = human`;
- referência à revisão;
- evidências preservadas;
- derivação explícita do valor anterior; e
- `overridden = true` somente quando o valor do produtor foi substituído.

Documento, claim e saída original permanecem imutáveis. Uma coleta posterior
com `input_fingerprint` diferente não herda silenciosamente o override:
revalida o fato e reabre a exceção quando necessário.

## 7. Persistência mínima

Duas tabelas são suficientes:

1. `data_quality_exceptions`: fila materializada e idempotente; e
2. `data_quality_reviews`: decisões append-only.

Motivos para não usar `discovered_opportunities`:

- a fila atual representa somente candidatos Web antes da promoção;
- FINEP, FAPESP e FAPESC não passam por ela; e
- misturar estado editorial com qualidade factual impediria revisar o caso
  Finep/Eureka sem falsificar sua origem.

Ambas são globais e administrativas, escritas por service role e sem policy de
leitura para usuários finais. A API pública recebe apenas a projeção segura,
nunca notas internas, identidade do revisor ou payload bruto da exceção.

Não criar tabela por campo, fonte ou tipo de sujeito. Índices adicionais
dependem de consulta real que os justifique.

## 8. Detecção e integração

```text
bundle/proveniência/produtor atual
  → validadores e detector determinístico
  → sem exceção: projeção atual segue normalmente
  → com exceção: upsert idempotente na fila
      → revisão humana append-only
      → projeção factual revisada
      → consumidores leem o mesmo estado corrente
```

O detector:

- opera sobre dados já adquiridos, sem rede ou LLM;
- roda após composição/proveniência e antes de publicar a projeção como fato
  confiável;
- pode ser reexecutado no ETL diário;
- não altera bundles nem evidências;
- não transforma erro operacional em decisão factual; e
- registra falha sanitizada sem conteúdo bruto.

Falha ao persistir uma exceção não pode tornar o fato confiável por padrão.
Para fatos temporais novos ou revalidados, o runtime mantém
`validity_state = needs_review` até conseguir registrar ou resolver o caso.

## 9. Consumo no produto

### 9.1 Operador

Reusar a área administrativa da Descoberta com uma seção ou aba
**Exceções de dados**, em vez de criar um segundo produto administrativo.

Cada item mostra:

- sujeito, fonte e campo;
- motivo em linguagem clara;
- valor atual e alternativas conflitantes;
- trecho, documento e data da versão;
- impacto no produto; e
- ações permitidas pelo contrato da §6.2.

Filtros mínimos: abertas/resolvidas, tipo de exceção e fonte. Não criar
atribuição, SLA, comentários, notificações ou workflow de equipe no pré-beta.

### 9.2 Radar, Ecossistema, Explorar e Escrita

Uma única projeção temporal deve ser usada pelos consumidores; a regra não será
reimplementada no frontend ou em prompts.

- `active`: pode aparecer como oportunidade acionável.
- `closed`: permanece histórico, mas não entra no match ativo.
- `needs_review`: não entra como oportunidade aberta no Radar; pode permanecer
  visível no Ecossistema com **Validade a confirmar**.
- Explorar e Escrita nunca afirmam que `needs_review` está aberto nem inventam
  prazo.
- A superfície exibe fonte e data da última verificação quando disponíveis.

O caso Finep/Eureka deve resultar em `needs_review` até que uma evidência
autoritativa confirme encerramento, novo prazo ou fluxo contínuo. O status
bruto `ABERTA` não basta.

## 10. Feedback para a spec 06

O feedback agregado inclui somente:

- quantidade de exceções por `issue_code`, fonte e papel documental;
- campos mais corrigidos;
- diferença entre valor produzido e revisado;
- locators/documentos associados; e
- resultado da revisão.

Esse diagnóstico permite distinguir:

- falha de aquisição;
- falha de extração de texto;
- falha de layout/tabela;
- documento escaneado/OCR; e
- contradição real da fonte.

A spec 06 só adicionará uma rota de extração quando houver casos medidos que
ela consiga resolver. Revisão humana não vira automaticamente golden de modelo:
incorporação em dataset exige curadoria e versionamento explícitos.

## 11. API, segurança e privacidade

Superfície administrativa mínima:

```text
GET  /data-quality/exceptions
GET  /data-quality/exceptions/{id}
POST /data-quality/exceptions/{id}/reviews
```

Os endpoints usam `AdminUserId`, payload Pydantic estrito e erros sanitizados.
Não aceitam `actor_id` enviado pelo cliente; a identidade vem da autenticação.

Não persistir:

- documento integral duplicado;
- headers, tokens, credenciais ou URLs assinadas;
- traceback ou resposta bruta de provedor;
- dados pessoais que não sejam necessários à auditoria; ou
- justificativa ilimitada — aplicar limite curto e explícito.

## 12. Observabilidade

Métricas diagnósticas:

- exceções abertas e resolvidas por código/fonte/campo;
- idade da fila;
- tempo até revisão;
- reaberturas após nova versão;
- decisões por tipo; e
- casos temporais impedidos de aparecer como ativos.

Não criar threshold ou gate bloqueante no primeiro rollout. O sistema mede o
baseline antes de qualquer SLA.

Logs carregam somente IDs, códigos e tipos de erro. Evidência, justificativa e
valor factual não são impressos.

## 13. Rollout e rollback

1. implementar contratos puros e fixture Finep/Eureka;
2. adicionar persistência administrativa;
3. rodar detector temporal em shadow e medir o estoque;
4. disponibilizar fila e revisão ao operador;
5. revisar casos `ABERTA + prazo ausente`;
6. habilitar a projeção temporal comum para novos/revalidados;
7. expor incerteza nas superfícies de produto; e
8. reconciliar métricas e documentação.

Antes do enforcement, registros legados permanecem identificados como
`unknown/legacy`; não são convertidos em fluxo contínuo. O rollout não exige
backfill exaustivo: prioriza oportunidades apresentadas como abertas.

Rollback desliga a leitura de reviews e retorna à projeção anterior, sem apagar
exceções ou decisões. O detector pode permanecer diagnóstico. Rollback não
altera bundles nem documentos.

## 14. Validação proporcional

Reutilizar a suíte `provenance` e os testes atuais; não criar harness paralelo.

Casos mínimos:

1. prazo futuro confirmado;
2. prazo vencido;
3. fluxo contínuo explicitamente comprovado;
4. aberto sem prazo nem evidência de continuidade — Finep/Eureka;
5. status encerrado sem prazo;
6. prazos conflitantes sem precedência;
7. correção humana com evidência;
8. tentativa de correção sem evidência;
9. recoleta idêntica idempotente; e
10. nova versão que reabre a exceção.

Uma fixture por comportamento basta. Testes não acessam rede, produção,
credenciais ou LLM real.

## 15. Não objetivos

- revisar manualmente todo o catálogo;
- corrigir conteúdo diretamente em bronze, bundle ou gold;
- substituir o gate editorial da Descoberta;
- mudar critérios de relevância ou matching;
- criar score probabilístico de confiança;
- treinar, ajustar prompt ou promover modelo automaticamente;
- implementar OCR, visão ou parsing de layout — responsabilidade da spec 06;
- criar sistema genérico de tickets, SLA ou colaboração;
- backfill integral de histórico; ou
- garantir correção jurídica além da evidência oficial adquirida.

## 16. Sequência lógica para o planejamento

Após aprovação desta spec, o plano executável deve decompor, em tasks pequenas:

1. contrato de exceção, revisão e temporalidade;
2. detector temporal com fixture Finep/Eureka;
3. persistência e repositório;
4. projeção revisada e integração conservadora com o runtime;
5. API e revisão administrativa;
6. comunicação de validade nos consumidores; e
7. métricas diagnósticas, validação e reconciliação.

O planejamento pode reduzir ou dividir essas etapas, mas não antecipar OCR,
workflow genérico ou revisão exaustiva de atores.

## 17. Critérios de aceite da spec

Esta proposta pode ser aprovada quando o proprietário aceitar que:

1. prazo ausente não significa fluxo contínuo;
2. `ABERTA` sem prazo ou evidência de continuidade exige revisão;
3. oportunidade com vigência incerta não aparece como ativa no Radar;
4. correção humana exige evidência versionada;
5. nova versão material pode reabrir uma decisão anterior;
6. a primeira vertical obrigatória é temporal; e
7. atores entram apenas por exceção concreta, sem campanha de revisão; e
8. oportunidade cujo prazo termina hoje permanece ativa até o fim do dia em
   `America/Sao_Paulo`.

## 18. Critérios de conclusão da implementação

A futura implementação estará concluída quando:

1. detector e fila cobrirem os casos temporais da §14;
2. decisões forem append-only, autenticadas e recuperáveis;
3. Finep/Eureka não puder permanecer ativa apenas por `status = ABERTA`;
4. fluxo contínuo exigir evidência explícita;
5. Radar, Ecossistema, Explorar e Escrita consumirem a mesma projeção;
6. falhas não vazarem conteúdo nem fabricarem certeza;
7. métricas alimentarem diagnóstico da spec 06 sem autoaprendizado;
8. testes proporcionais e gates herdados estiverem verdes; e
9. documentação autoritativa estiver reconciliada.
