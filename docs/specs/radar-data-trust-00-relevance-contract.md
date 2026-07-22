# Radar Data Trust 00 — Contrato de relevância

**Status:** proposta para aprovação · **Data:** 2026-07-21
**Spec-mãe:** [`radar-data-trust.md`](radar-data-trust.md)
**Ordem:** 00 · **Bloqueia:** todas as demais specs Radar Data Trust
**Perfis afetados:** usuário de produto e operador
**Impacto:** médio; classificação, triagem, métricas e comunicação de cobertura

---

## 1. Problema

“Mapear o ecossistema brasileiro de inovação” não é um denominador operacional:
o país possui órgãos, fundações, empresas, programas e publicações demais para
uma promessa verificável de exaustividade. Sem uma tese explícita, a Descoberta
pode aumentar volume sem aumentar valor, e métricas de recall ou cobertura não
têm universo de referência.

O produto atende **startups e pequenas e médias empresas de base tecnológica**.
A unidade de decisão é a oportunidade individual, não o órgão que a publicou.
Uma FAP pode publicar bolsas acadêmicas fora do escopo e, no mesmo portal, uma
chamada relevante para empresas.

Esta spec define o contrato de relevância usado por busca, triagem, revisão
humana, goldens e comunicação do produto. Ela não define ainda como cada campo
é extraído nem quais fontes devem ser monitoradas.

## 2. Resultado pretendido

Para qualquer candidato, um operador ou avaliador deve conseguir produzir uma
das decisões:

- `in_scope`: a oportunidade pertence à tese do Radar;
- `out_of_scope`: há evidência suficiente de que não pertence; ou
- `needs_review`: o material disponível não permite decisão segura.

A decisão deve registrar reason codes e evidência. Casos ambíguos não são
convertidos silenciosamente em rejeição: falso negativo é o erro de maior custo
na Descoberta.

O vocabulário de decisão é compartilhado, mas os critérios são específicos por
`kind`. O classificador de uma oportunidade não pode ser reutilizado como se
investidor, ICT, agência ou programa institucional fossem chamadas abertas.

## 3. Definições

### 3.1 Empresa-alvo

Organização empresarial brasileira, constituída ou em formação quando a chamada
permitir, que desenvolve ou aplica tecnologia como componente material de seu
produto, processo, serviço ou modelo operacional. Inclui startups, micro,
pequenas e médias empresas; chamadas que também aceitam grandes empresas não
são excluídas se o público-alvo puder participar em condições reais.

“Base tecnológica” é avaliada pela finalidade e pelos requisitos da
oportunidade, não por CNAE isolado ou autodeclaração promocional.

### 3.2 Oportunidade acionável

Instrumento com caminho concreto de candidatura, seleção, parceria ou acesso a
benefício. Deve existir chamada, regulamento, formulário, fluxo contínuo ou
orientação operacional equivalente. Notícia, evento ou descrição institucional
sem caminho de participação não basta.

### 3.3 Benefício relevante

Ao menos um benefício material ligado a inovação:

- recurso não reembolsável ou subvenção econômica;
- prêmio financeiro ou contrato/piloto decorrente de desafio tecnológico;
- acesso subsidiado a laboratório, ICT, infraestrutura ou serviço tecnológico;
- cooperação de P&D com participação empresarial;
- aceleração ou incubação com seleção, suporte estruturado e benefício concreto;
- apoio técnico, regulatório ou de internacionalização vinculado a projeto de
  inovação, quando houver seleção e entrega verificável.

Mentoria genérica, networking ou exposição, isoladamente, não bastam.

## 4. Regra de inclusão

Uma oportunidade é `in_scope` quando todas as condições obrigatórias são
satisfeitas:

| Código | Condição | Evidência mínima |
|---|---|---|
| `R1_ENTERPRISE_PATH` | startup/PME/empresa pode candidatar-se, liderar ou participar como beneficiária/parceira material | trecho de público elegível, proponentes ou composição exigida |
| `R2_TECH_INNOVATION` | a finalidade envolve desenvolvimento, adoção, validação ou comercialização de inovação/tecnologia | objetivo, tema, desafio ou requisito técnico |
| `R3_ACTIONABLE` | existe caminho concreto de inscrição, seleção, credenciamento ou participação | link/regulamento, fluxo, prazo ou instrução operacional |
| `R4_RELEVANT_BENEFIT` | existe ao menos um benefício relevante da §3.3 | condições financeiras, piloto, infraestrutura ou programa estruturado |
| `R5_BRAZIL_RELEVANCE` | empresas brasileiras podem participar ou o benefício produz efeito operacional no Brasil | abrangência, sede elegível ou regra de participação |

Uma empresa não precisa ser a única categoria elegível. Consórcios empresa–ICT
e chamadas lideradas por ICT permanecem no escopo quando a empresa possui papel
material e benefício verificável.

## 5. Exclusões explícitas

| Código | Fora do escopo | Regra |
|---|---|---|
| `X1_ACADEMIC_ONLY` | bolsa ou auxílio exclusivamente para estudante, pesquisador ou instituição científica | excluir quando empresa não possui caminho material de participação |
| `X2_CONVENTIONAL_CREDIT` | empréstimo, financiamento, garantia ou linha de crédito convencional | excluir mesmo quando oferecido por banco público; crédito não é proposta deste produto |
| `X3_GENERIC_PROCUREMENT` | compra pública comum de produto/serviço | excluir salvo instrumento explícito de inovação, encomenda tecnológica, sandbox ou desafio com desenvolvimento/piloto |
| `X4_EVENT_CONTENT` | evento, webinar, curso, notícia ou conteúdo editorial | excluir se não houver seleção e benefício acionável |
| `X5_GENERIC_SUPPORT` | mentoria, networking ou comunidade sem entrega material e seleção verificável | excluir |
| `X6_NON_TECH` | apoio empresarial sem relação material com inovação ou tecnologia | excluir |
| `X7_NO_ENTERPRISE_PATH` | programa institucional ou acadêmico sem participação empresarial | excluir |
| `X8_INVESTOR_DIRECTORY` | notícia de rodada, tese de fundo ou diretório de VC | não tratar como oportunidade da Descoberta; investidores continuam entidades curadas do Ecossistema |

`X8_INVESTOR_DIRECTORY` exclui somente a tentativa de publicar o registro como
**oportunidade**. Não aprova o investidor como ator nem o dispensa da triagem
específica da §8.

### 5.1 Não excluir por instituição

Nenhum reason code desta seção autoriza blacklist de domínio, órgão ou portal.
A exclusão pertence ao candidato/URL/documento avaliado. O cache negativo pode
evitar custo repetido para a mesma versão, mas deve expirar conforme contrato
da Descoberta.

## 6. Casos limítrofes

| Caso | Decisão padrão | Condição para mudar |
|---|---|---|
| aceleração com equity | `needs_review` | `in_scope` se houver seleção e benefício tecnológico/empresarial material; equity deve ser transparente |
| programa sem recurso financeiro | `needs_review` | `in_scope` se entregar infraestrutura, piloto, parceria ou suporte estruturado de valor verificável |
| bolsa com empresa parceira | `needs_review` | `in_scope` somente se a empresa tiver papel/benefício material além de hospedar o bolsista |
| chamada apenas para ICT, com futura transferência a empresas | `out_of_scope` | muda se empresa puder participar do instrumento atual |
| encomenda tecnológica ou CPSI | `needs_review` | `in_scope` quando houver desenvolvimento/piloto inovador acessível à empresa-alvo |
| benefício fiscal | `needs_review` | exige caminho acionável e aderência clara ao público; não é automaticamente edital |
| prêmio/desafio sem contrato garantido | `needs_review` | `in_scope` se benefício e regras de seleção forem concretos |
| fluxo contínuo sem deadline | não é motivo de exclusão | `R3_ACTIONABLE` pode ser satisfeito por procedimento vigente |
| chamada encerrada | relevância e vigência são dimensões distintas | pode permanecer `in_scope` como histórico; não aparece como aberta |
| grande empresa também elegível | não é motivo de exclusão | empresa-alvo precisa continuar elegível ou parceira material |

Casos recorrentes devem virar regra ou exemplo nesta spec e caso no golden; não
devem permanecer indefinidamente como decisão informal do operador.

## 7. Contrato de classificação

### 7.1 Saída conceitual

```json
{
  "decision": "in_scope | out_of_scope | needs_review",
  "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
  "exclusion_codes": [],
  "evidence": [
    {
      "code": "R1_ENTERPRISE_PATH",
      "quote": "...",
      "source": "landing_page | edital | anexo",
      "locator": {"document": "Edital.pdf", "page": 3}
    }
  ],
  "missing_information": [],
  "classifier_version": "radar-data-trust-relevance-v1"
}
```

Esta forma é contrato lógico. A spec de proveniência define o tipo compartilhado
de evidência; a implementação não deve criar um segundo formato incompatível.

### 7.2 Precedência

1. Evidência de `X1`–`X8` pode decidir `out_of_scope` quando inequívoca.
2. Na ausência de exclusão inequívoca, todos `R1`–`R5` são necessários para
   `in_scope`.
3. Informação ausente, documento inacessível ou conflito produz
   `needs_review`, não `out_of_scope`.
4. Classificação de relevância não altera `status` temporal nem substitui
   revisão de elegibilidade da empresa específica.

## 8. Relação com as superfícies atuais

### Descoberta

- A busca pode ser ampla e retornar candidatos fora do escopo.
- A triagem usa este contrato para reduzir ruído sem maximizar precisão às
  custas de falsos negativos.
- `needs_review` entra no staging e exige decisão humana.
- `out_of_scope` só pode alimentar cache negativo com reason code e versão.

### Gold e Ecossistema

- Apenas conteúdo aprovado e `in_scope` entra como oportunidade publicada.
- Entidades de ecossistema, como investidores e ICTs, podem existir no gold sem
  serem oportunidades cobertas por esta classificação.
- Histórico relevante pode permanecer no catálogo, claramente marcado como não
  vigente.

#### Fronteira entre oportunidade e ator/catálogo existente

Este contrato governa candidatos da Descoberta e novos registros tratados como
**oportunidade acionável**. A mesma triagem não deve reclassificar automaticamente:

- investidores existentes;
- ICTs EMBRAPII;
- agências; ou
- programas e catálogos versionados que já possuem produtor próprio.

Essas entidades podem ser relacionadas a uma oportunidade `in_scope`, mas
precisam de **triagem própria por tipo**, não de isenção. Um programa
institucional e uma chamada acionável desse programa são registros
conceitualmente distintos mesmo quando compartilham nome ou organização.

O estado atual de EMBRAPII e Investidores é ponto de partida, não selo de
qualidade: o primeiro reflete coleta ampla do portal; o segundo contém extração
LLM de páginas oficiais com curadoria básica. Ambos devem permanecer
`legacy/unknown` nos campos ainda não revalidados.

##### Critérios mínimos para ICT

- identidade e vínculo institucional verificáveis;
- capacidade de cooperação tecnológica com empresas;
- competências, localização e status sustentados por fonte oficial; e
- atualização ou data de verificação explícita.

##### Critérios mínimos para investidor

- identidade e página oficial verificáveis;
- atuação material com startups/empresas de tecnologia;
- relevância para empresas brasileiras ou operação no Brasil; e
- tese, estágio, setores, geografia e ticket marcados como `unknown` quando não
  houver evidência, sem completar por plausibilidade da LLM.

##### Critérios mínimos para programa, agência e demais atores

- identidade e operador verificáveis;
- relação demonstrável com uma oportunidade ou mecanismo relevante; e
- campos específicos validados conforme schema próprio do `kind`.

Cada tipo terá reason codes e golden próprios na implementação. Um único prompt
genérico para todos os atores é proibido até avaliação demonstrar equivalência.

### Explorar, Radar e Escrita

- Explorar pode explicar por que uma oportunidade pertence ao escopo.
- Radar continua avaliando fit e elegibilidade da empresa; `in_scope` não
  significa “adequado para você”.
- Escrita só usa oportunidade publicada e documentos autorizados.

## 9. Golden e métricas

### 9.1 Corpus mínimo antes de automatizar bloqueio

O golden deve conter exemplos reais, revisados pelo proprietário do produto,
estratificados por:

- `in_scope`, `out_of_scope` e `needs_review`;
- oportunidades, ICTs, investidores, programas e agências, avaliados por
  critérios próprios;
- DOU, FAP/DOE, agência federal, corporate/open innovation e aceleração;
- edital, desafio e programa;
- empresa direta, consórcio empresa–ICT e acadêmico-only;
- subvenção, prêmio/piloto, parceria, aceleração e crédito; e
- páginas claras, hubs, snippets ambíguos e documentos incompletos.

O número e os thresholds serão aceitos em
`radar-data-trust-02-quality-gates.md`. Esta spec proíbe promover a classificação
a gate oficial apenas com os casos atuais sem revalidação de representatividade.

### 9.2 Métricas necessárias

- recall de `in_scope`, com falso negativo destacado;
- precisão de `out_of_scope`;
- taxa de encaminhamento a `needs_review`;
- acurácia por reason code;
- concordância humano–classificador; e
- rendimento após revisão por fonte/query.

Uma média global não pode ocultar perda de uma categoria, região ou mecanismo.

## 10. Tasks de implementação previstas

Estas tasks só começam após aprovação desta spec e devem ser detalhadas em um
plano executável antes de delegação:

1. **RT00-T01 — Contrato de domínio:** introduzir enums/tipos versionados sem
   alterar ainda a promoção; cobrir a fronteira entre oportunidade e
   ator/catálogo curado.
2. **RT00-T02 — Golden:** migrar/expandir os casos de triagem para decisões e
   reason codes desta spec, com datasets separados por `kind`.
3. **RT00-T03 — Classificadores por tipo:** adaptar prompt e parser da triagem de
   oportunidades com `needs_review` explícito; criar validadores/evaluadores
   separados para atores, começando em shadow e sem prompt genérico único.
4. **RT00-T04 — Staging:** persistir decisão, reason codes, versão e evidência;
   não transformar falha transitória em exclusão.
5. **RT00-T05 — Operação:** exibir justificativa e informações faltantes na fila
   administrativa.
6. **RT00-T06 — Métricas:** produzir relatório estratificado; bloqueio fica para
   a spec 02.
7. **RT00-T07 — Reconciliação:** atualizar schema autoritativo, arquitetura e
   runbook depois que o runtime estiver comprovado.

Cada task deve preservar o gate humano e não promover automaticamente
candidatos classificados como `in_scope`.

Os planos executáveis vivem em
`docs/execution/radar-data-trust/plans/00-relevance/`.

## 11. Rollout e compatibilidade

- O classificador novo inicia em shadow sobre candidatos já revisados.
- Decisões existentes não são reescritas sem backfill explícito e versionado.
- `None`/erro transitório da triagem continua sendo falha operacional, nunca
  `out_of_scope`.
- Durante migração, registros sem classificação v1 aparecem como `unclassified`.
- Cache negativo anterior não é convertido automaticamente para reason codes.
- Após avaliação aceita, a classificação nova pode orientar staging, mantendo
  revisão humana obrigatória.

Rollback desativa o produtor v1 e preserva os campos aditivos já gravados; não
apaga decisões humanas nem histórico.

## 12. Não objetivos

- enumerar todos os órgãos, FAPs, municípios ou empresas do Brasil;
- definir lista fechada de fontes;
- medir cobertura absoluta do ecossistema;
- decidir fit de uma empresa específica;
- extrair todos os campos do edital;
- definir precedência entre retificações; e
- transformar investidor, ICT ou programa institucional em oportunidade apenas
  para aumentar volume.

## 13. Critérios de aceite da spec

Esta spec está pronta para implementação quando:

1. público-alvo, inclusões, exclusões e casos limítrofes forem aprovados pelo
   proprietário do produto;
2. `in_scope`, `out_of_scope` e `needs_review` tiverem semântica inequívoca;
3. reason codes forem suficientes para rotular o golden inicial;
4. estiver aceito que classificação ocorre por oportunidade, nunca por órgão;
5. estiver aceita a separação entre relevância, vigência e fit; e
6. a spec 01 reutilizar este contrato sem criar uma taxonomia paralela.

## 14. Critérios de conclusão da implementação

A futura implementação só pode reconciliar esta spec como vigente quando:

- o mesmo classificador versionado rodar em produção e no harness;
- golden e métricas estratificadas estiverem publicados;
- staging preservar decisão, razões e evidência;
- falhas e ambiguidades chegarem à revisão humana;
- nenhuma exclusão institucional tiver sido introduzida; e
- documentação autoritativa descrever o comportamento comprovado.
