<!-- SEED / DRAFT (2026-06-14) — playbook do mecanismo `credito` (financiamento
     REEMBOLSÁVEL). COMPETÊNCIA (craft), não conhecimento. Delta sobre a persona-base
     (system prompt do Redator). Fato do edital → RAG; fato de praxe → rationale curto.
     NUNCA taxa/prazo/carência/garantia%/índice-de-corte/rubrica (muda por edital → RAG).
     Cada `##` é um TIPO e roteia para um consumidor. Carregado por core.skills.load_playbook (loader ativo 2026-06-14); SEED até validação por outcome.
     Destilado de entrevista a especialistas + 2 LLMs (2026-06-14); pendente validação
     por outcome real (learning loop, BACKLOG). -->

# Playbook — Crédito (financiamento reembolsável)

A lente: a empresa **paga de volta**. O avaliador **não é parecerista de mérito —
é analista de crédito**; ele não compra ideia, inovação ou potencial, **compra
previsibilidade de repagamento e solvência**, e teme uma coisa: **inadimplência**.
A inovação deixa de ser protagonista (nota) e vira **bilhete de entrada**
(elegibilidade para a taxa subsidiada). **A inversão que explica tudo:** na
subvenção você convence que *o risco merece ser financiado*; no crédito, que *o
risco já foi suficientemente reduzido* para merecer dívida. Escreva como um CFO
sóbrio, não como um CTO empolgado.

## Padrões de escrita e tom  <!-- → Redator (geração) -->

**O arco** (não é "dor → gargalo tecnológico → redução de risco"):
> capacidade/tração comprovada → oportunidade economicamente validada →
> investimento → ganho previsível de receita/margem/eficiência → **geração de caixa
> incremental → serviço da dívida com folga (sobrevive a stress)**.

**Demonstre FOLGA, não só viabilidade** *(o credor não procura "conseguimos
pagar"; procura "pagamos mesmo se algo der errado" — é a folga que afasta o medo
da inadimplência)*:
- Posicione a inovação como **redutora** de risco do negócio (fosso que baixa custo
  unitário, defende margem), **nunca** como fonte de risco. Exaltar a incerteza
  tecnológica — virtude na subvenção — aqui **lê como "risco de ruína"**.
- Conservadorismo é **virtude**, não fraqueza: projeção que sobrevive a um cenário
  adverso vale mais que projeção otimista.

**Craft por seção** (o que cada uma precisa FAZER; o erro que reprova):

| Seção | O trabalho real | Erro que mata |
|---|---|---|
| Sumário / objeto | ligar o recurso ao salto de receita/margem | descrever a compra/tecnologia sem o retorno |
| Empresa / histórico | provar que é **boa pagadora** (recorrência, governança, sobrevive a crise) | contar história institucional; esconder passivo/endividamento (o credor acha) |
| Mérito / inovação | inovação como **fosso que reduz** o risco do negócio (↓custo, ↑margem) | escrevê-la como subvenção (exaltar incerteza) → lê como risco de ruína |
| Mercado / plano | tração **real**: contratos, LOIs, pipeline, recorrência | top-down ("1% de mercado gigante"); receita que nasce na planilha |
| Projeção financeira | premissas explícitas, **derivadas do histórico**, que sobrevivem a stress | planilha mágica (receita triplica, custos/CAC flat) |
| Uso / cronograma de desembolso | alinhar desembolso ao CAPEX e ao repagamento | lista contábil; carência incoerente com o início do faturamento |
| Garantias | dar conforto ao credor + sinalizar **skin in the game** | oferecer intangível/ativo depreciado como garantia principal |
| Capacidade de pagamento | demonstrar folga sob cenário adverso | afirmar viabilidade sem stress; otimismo sem lastro |

**Cadeia causal** — amarre numa corrente verificável: `investimento → capacidade/
eficiência → receita/margem → caixa operacional → serviço da dívida`. A incoerência
que mais reprova: a dívida financia o **novo** (CAPEX de P&D) mas quem paga é o
negócio **velho** estagnado — *o projeto não se paga*; ou receita +300% com
capacidade +10%.

**Língua** — financeira, auditável, ancorada no histórico. Aplique **vago →
específico** (números são ilustração, vêm do caso real — não invente):
- "temos boa saúde financeira" → "geração operacional de caixa positiva nos últimos
  exercícios, com folga para absorver o novo serviço da dívida"
- "o mercado é grande" → "demanda reprimida na carteira atual equivalente a 28% da
  capacidade instalada"
- "o projeto aumentará o faturamento" → "amplia a capacidade em 40%, com receita
  projetada de 18–22% em linha com a demanda já observada"
- "conseguiremos pagar o financiamento" → "mesmo sob queda de 20% na receita
  projetada, a geração de caixa cobre o serviço da dívida"
- "o projeto é altamente rentável" → "payback estimado de X anos e incremento de
  Y p.p. de margem operacional"

**Defenda a projeção ancorando no passado:** "a empresa cresce ~15%/ano sem o
projeto; logo, projetar 25% com o projeto é conservador e auditável" > qualquer
hockey-stick.

## Heurísticas de aprovação  <!-- → ComplianceMonitor (avaliação) -->

- **Régua sequencial e impiedosa:** capacidade de pagamento (histórico/geração de
  caixa) + qualidade das garantias decidem primeiro; depois robustez/conservadorismo
  da projeção; **inovação por último — é elegibilidade, não nota**. A inovação
  raramente salva um crédito ruim; um crédito sólido sobrevive com inovação moderada.
- **Inovação crível e previsível > inovação revolucionária** (revolucionária, sem
  certeza de que funciona, lê como risco de ruína para o credor).
- Pesos reais ≠ edital: capacidade de pagamento + histórico financeiro + garantias =
  altíssimo; qualidade/conservadorismo da projeção = alto; mercado = médio;
  originalidade tecnológica = baixo.
- **"Sim com convicção"** = a projeção sobrevive a um stress test de receita **e ainda
  paga**, histórico limpo, garantias líquidas. **"Sim com ressalvas"** = aprovado, mas
  com exigências (mais garantia, aval dos sócios, trava de recebíveis, corte de
  valor/prazo) — sinal de que a folga não ficou demonstrada.

## Anti-padrões / red flags  <!-- → ComplianceMonitor (avaliação) -->

Instant kills (sinalize forte):
- **Fluxo de caixa não cobre o serviço da dívida** (morte imediata).
- **Queima de caixa** (histórico deficitário) pedindo dívida para prolongar runway —
  credor financia expansão, não runway.
- **Hockey-stick sem lastro**: receita dispara sem capacidade/conversão que explique.
- **Desvio de finalidade**: recurso de inovação (barato) para tapar capital de giro /
  reperfilar dívida cara.
- Dependência de **cliente único**; restrição cadastral; "contabilidade criativa"
  (mútuos de sócios bagunçados, passivo tributário escondido).
- **Inovação experimental demais** (núcleo ainda não validado) — na dívida, risco de ruína.

Erros que **boas empresas** cometem por desconhecer a praxe:
- **Escrever como subvenção** (o erro mais frequente): exaltar o desafio/incerteza
  tecnológica. Onde o parecerista científico via "fronteira do conhecimento", o
  analista de crédito lê "risco de inadimplência".
- Tratar **conservadorismo como fraqueza** (o analista valoriza prudência).
- **Otimismo como virtude**: "vamos dominar o mercado" é lido como ausência de gestão
  de risco. O credor prefere +10% com certeza a +100% na esperança.
- **Esconder riscos** — o analista sabe que existem; melhor expor a mitigação.

Termos que deslocam para o gênero errado:
- P&D incerto (assusta o credor): "pesquisa exploratória", "estudo de viabilidade",
  "investigação", "hipótese aberta", "vamos testar", "pivotar", "prova de conceito"
  como núcleo do pleito.
- Pitch/VC (assusta o credor): "hipercrescimento", "dominar o mercado", "unicórnio",
  "estratégia de exit", "crescimento exponencial".

---
**Fato (NÃO entra aqui — vem do edital via RAG):** taxa/indexador, prazo, carência,
% financiável, contrapartida exigida, tipos e % de garantia aceitos (FGI/fiança/
imóvel…), índices financeiros de corte exigidos, ROB/porte elegível, teto/piso de
valor, rubricas financiáveis, exigências cadastrais (CADIN/CND), documentação.
*Regra de bolso: se muda ao trocar "Inovacred 2024 → BNDES 2026", é fato (RAG); se
sobrevive à troca, é craft (fica aqui).*
