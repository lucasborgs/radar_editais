# Radar Data Trust 07 — Promoção incremental da extração adaptativa

**Status:** proposta para aprovação  
**Data:** 2026-08-10  
**Spec-mãe:** [`radar-data-trust-06-adaptive-extraction.md`](radar-data-trust-06-adaptive-extraction.md)

## 1. Problema

A RT06 já introduziu extração textual unificada, artifacts versionados,
fingerprint/cache, evidências, composição documental pela RT04, revisão de
exceções pela RT05 e um read model comum aos consumidores.

O que falta não é uma nova plataforma de governança. Falta tornar esse fluxo a
fonte efetiva dos fatos decisórios do Radar, sem reprocessar documentos
inalterados e sem manter produtores concorrentes para o mesmo campo.

A promoção precisa conservar quatro garantias:

1. somente fatos estruturados e evidenciados entram na projeção efetiva;
2. incerteza não é convertida em ausência ou certeza;
3. mudanças materiais reprocessam somente os documentos afetados; e
4. falha ou conflito não apaga a última projeção saudável.

## 2. Resultado pretendido

Tornar a extração adaptativa a fonte única dos fatos decisórios cobertos pela
RT06:

- elegibilidade, requisitos, exclusões, entidades elegíveis e público-alvo;
- prazo, janela de submissão e fluxo contínuo;
- valores, limites e contrapartida; e
- referências de tabelas que sustentem esses fatos.

KG, Knowledge, caminhos da consultoria e Writing devem consumir a mesma
projeção efetiva. O read model temporal continua responsável por
`active|closed|needs_review`; extrair uma data não declara validade.

## 3. Fluxo

```text
SourceBundle corrente
→ reutilizar artifact quando o fingerprint não mudou
→ executar text-v9 uma vez quando houver mudança material
→ validar schema, estado e evidências
→ persistir artifact
→ RT04 compor os documentos do bundle
→ RT05 tratar conflitos materiais
→ publicar a projeção efetiva
→ KG, Knowledge, caminhos e Writing consumirem a mesma projeção
```

O produtor recebe, em uma única extração por documento, todos os alvos
aplicáveis. Não haverá uma etapa anterior para classificar cobertura campo a
campo: descobrir semanticamente se o documento cobre um campo repetiria parte
do trabalho da própria extração e fragmentaria o produtor unificado.

## 4. Unidade incremental e identidade

A unidade de processamento é o documento canônico dentro do `SourceBundle`.

O fingerprint material existente deve considerar, no mínimo:

- identidade do sujeito e do documento;
- hash do conteúdo e do bundle;
- versão do schema e do produtor; e
- conjunto de alvos solicitado.

Fingerprint inalterado reutiliza o artifact saudável existente. Qualquer
mudança material produz uma nova tentativa append-only. Não haverá ledger
separado de cobertura nem cópia de claims para representar herança.

Um documento inalterado é reaproveitado pelo cache. Um novo documento é
extraído e composto com os artifacts dos demais documentos pela RT04. Uma
retificação é tratada como documento autoritativo do bundle e reprocessada
integralmente para os alvos aplicáveis; a precedência continua pertencendo à
RT04.

## 5. Gate mínimo de publicação

Cada claim é avaliado automaticamente. Um claim `stated` pode entrar na
projeção efetiva quando:

- respeita o schema canônico do campo;
- possui evidência resolvível no documento corrente;
- a evidência referencia conteúdo ou hash canônico verificável;
- o valor estruturado satisfaz as invariantes básicas do tipo, como data,
  moeda, intervalo ou percentual; e
- não existe conflito documental material aberto para o mesmo fato.

Não será criado score universal de confiança, classificador adicional de
cobertura ou política de limiares estatísticos para o runtime.

### Estados factuais

- `stated`: publica automaticamente quando passa pelo gate mínimo;
- `unknown`: permanece como lacuna e não exige revisão humana;
- `absent`: registra ausência explícita sem fabricar valor e não elimina
  elegibilidade automaticamente;
- `inferred`: pode apoiar exploração, mas não se torna fato decisório;
- `conflicting`: não publica o valor conflitante e encaminha o caso à RT05.

Claims válidos de um campo não são bloqueados por lacunas de outros campos.
Falha de extração, artifact parcial ou documento indisponível preserva a última
projeção saudável, quando houver.

## 6. Composição, revisão e autoridades

As responsabilidades permanecem concentradas nos módulos existentes:

- a RT06 interpreta o documento e produz claims evidenciados;
- a RT04 seleciona e compõe os documentos autoritativos do bundle;
- a RT05 resolve somente conflitos e correções materiais;
- o read model temporal calcula `active|closed|needs_review`; e
- o read model adaptativo entrega a projeção efetiva aos consumidores.

Revisão humana não é etapa normal de publicação. Ela ocorre somente quando um
conflito ou ambiguidade documental puder alterar elegibilidade, prazo, valor ou
uma recomendação apresentada ao usuário. `unknown`, falhas técnicas e campos
não aplicáveis não entram automaticamente em fila humana.

## 7. Ativação adaptativa por família

A autoridade fica encapsulada no read model e é controlada somente por
`RADAR_ADAPTIVE_ACTIVE_FAMILIES`, default vazio. Os valores permitidos são
`eligibility`, `temporal`, `financial` e `table_evidence`.

Para uma família inativa, o read model retorna exclusivamente o candidato
legado e não consulta artifacts, bundle ou RT05. Para uma família ativa, ele
seleciona artifacts compatíveis com o produtor, schema e targets correntes,
compõe RT04/RT05 e nunca consulta nem compõe o produtor legado.

Falha, indisponibilidade ou lacuna de uma execução adaptativa preserva o
artifact/projeção adaptativa saudável anterior quando disponível. Se não houver
snapshot saudável, o read model retorna uma projeção adaptativa com lacunas;
isso não autoriza fallback ao legado.

Não há ledger, promoção, rollback ou autoridade pública separados. Todos os
consumidores consultam o mesmo read model, e uma família pode permanecer
inativa sem bloquear as demais. A remoção física do legado não faz parte desta
mudança; o requisito operacional é impedir seu uso quando a família estiver
ativa.

## 8. Avaliação proporcional

A validação reutiliza a suíte `extraction` existente e um corpus pequeno,
versionado e representativo. O legado não é golden e equivalência com ele não é
critério de promoção.

A avaliação inicial verifica somente:

- correção dos valores estruturados;
- fidelidade e resolução das evidências;
- abstenção quando o documento não permite decidir;
- composição de múltiplos documentos e retificações; e
- consumo consistente por KG, Knowledge, caminhos e Writing.

Não são necessários plataforma de calibração, aprovação humana por família,
baseline econômico artificial, amostragem contínua ou suíte completa por
padrão. Métricas operacionais simples — artifacts reutilizados, chamadas LLM,
falhas, conflitos e lacunas — são suficientes para observar o fluxo.

## 9. Interface do módulo

O seam externo permanece o read model adaptativo: consumidores pedem a
projeção efetiva de um sujeito e não conhecem configuração de autoridade,
prompts, attempts, artifacts, precedência documental ou revisão.

O módulo deve esconder:

- reutilização por fingerprint;
- execução do produtor;
- persistência append-only;
- validação mínima;
- composição RT04/RT05; e
- seleção da projeção adaptativa saudável da família solicitada; e
- seleção interna entre legado e adaptativo.

Não criar interfaces públicas separadas para cobertura, herança, promoção,
rollback ou autoridade por campo, consumidor ou família.

## 10. Critérios de aceite

1. Documento inalterado reutiliza artifact saudável sem nova chamada LLM.
2. Documento materialmente alterado gera nova tentativa e extração unificada.
3. `stated` só é publicado com schema e evidência resolvível.
4. `unknown`, `absent`, `inferred` e `conflicting` preservam suas semânticas sem
   fabricar fato decisório.
5. RT04 compõe documentos e retificações sem escolher autoridade por
   `created_at`.
6. Somente conflitos materiais de elegibilidade, prazo, valor ou recomendação
   chegam à RT05 para decisão humana.
7. Falha ou artifact incompleto preserva a última projeção saudável.
8. KG, Knowledge, caminhos e Writing consomem a mesma projeção efetiva.
9. Família inativa consulta somente o legado e não consulta artifacts adaptativos.
10. Família ativa consulta somente a projeção adaptativa e não faz fallback
    silencioso ao legado.

## 11. Fora de escopo

- ledger de cobertura ou decisões `publish|inherit|hold`;
- promoção, rollback ou ledger de autoridade por campo, consumidor ou família;
- thresholds globais, calibração estatística ou score de confiança da LLM;
- aprovação humana genérica de artifacts ou famílias;
- backfill nacional obrigatório;
- OCR, layout ou visão sem perda medida;
- DeepResearch como fonte substituta de documento normativo;
- alterar a autoridade temporal;
- criar harness paralelo;
- promover `channel` sem entrada real; e
- criar novas colunas gold automaticamente para referências de tabela.

## 12. Decisões eliminadas da versão anterior

Esta versão substitui deliberadamente:

- decisão de cobertura por sujeito × documento × família × campo;
- `CoverageDecision` e ledger append-only próprio;
- estados de cobertura paralelos ao estado factual;
- gate intermediário `publish|inherit|hold`;
- herança representada como decisão persistida;
- rollout e rollback independentes por campo/consumidor;
- tickets obrigatórios de validadores extensivos e política de limiares; e
- revisão humana de todo caso retido.

Essas estruturas poderão ser reconsideradas somente se uso real, volume ou
incidentes demonstrarem que as garantias existentes são insuficientes.
