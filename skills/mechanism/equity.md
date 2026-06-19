<!-- SEED / DRAFT (2026-06-14) — playbook do mecanismo `equity` (captação de risco).
     GÊNERO OUTBOUND: não é aderência a edital — é fit com a TESE do fundo-alvo.
     COMPETÊNCIA (craft de storytelling), não conhecimento. Roteado ao agente de PITCH
     (mode=pitch), NÃO ao caminho de compliance do edital (D4). Aqui NÃO há RAG de
     fomento: o "fato" é dado do deal/empresa (valuation, métricas), não do edital.
     Cada `##` é um TIPO e roteia para um consumidor. Carregado por core.skills.load_playbook (loader ativo 2026-06-14); SEED até validação por outcome.
     Destilado de entrevista a especialistas + 3 LLMs (2026-06-14); pendente validação
     por outcome real (learning loop, BACKLOG). -->

# Playbook — Equity (pitch a investidor / captação de risco)

A lente: o investidor pensa em **power law** — não procura "vai dar certo com
segurança", procura **upside assimétrico** (o negócio capaz de devolver o fundo
inteiro). Compra **time × mercado × tração × momentum**; teme acima de tudo o negócio
**"lifestyle/zumbi"** (saudável, lucrativo, cresce devagar, nunca dá liquidez). A
inversão que organiza tudo: o mesmo *"vamos crescer 100x"* que **mata** um pleito de
crédito é o que **liga** o investidor — **se houver lastro**. Gênero **outbound**: não
há "edital a cumprir"; o que condiciona o texto é a **tese do fundo-alvo**. Regra de
ouro: *investidores não financiam projetos nem previsões — financiam a possibilidade de
um time excepcional capturar uma oportunidade excepcionalmente grande num momento
excepcionalmente favorável.*

## Padrões de escrita e tom  <!-- → Redator de pitch (PITCH_WRITER_AGENT, mode=pitch) -->

**O arco** (mercadológico e exponencial, não metodológico):
> **why now** (mudança estrutural) → problema grande e urgente → **insight/unfair
> advantage** → produto que materializa o insight → **tração que PROVA o insight** →
> mercado gigante (bottom-up) → time que executa → caminho ao retorno assimétrico.

**"Why now" é o elo subestimado** *(o investidor adora a empresa que parece inevitável
porque o mundo mudou — inflexão tecnológica, regulatória ou de comportamento; "uma
mudança criou uma oportunidade que poucos perceberam")*.

**Ambição com lastro** — *"if you say it, prove it"*: cada afirmação vira
`statement → proof point → impact`. Ambição extrema **×** evidência concreta. O
investidor tolera ambição; não tolera fantasia.

**Craft por slide** (o trabalho real; o erro que mata):

| Slide | O trabalho real | Erro que mata |
|---|---|---|
| Problema | dor *hair-on-fire* (o cliente já paga p/ resolver) | dor "vitamina"; problema inventado |
| Solução | a transformação e por que **agora é possível** | tour de features técnicas |
| Insight / unfair advantage | por que VOCÊ enxerga/tem o que os outros não | achar que *first-mover* é moat |
| Mercado (TAM) | grande e crível, **bottom-up** (unidades × ticket) | top-down ("1% de um mercado de trilhões") |
| Tração | a **prova**; retenção > aquisição | *vanity metrics* (downloads, seguidores) |
| Modelo de negócio | unit economics + por que **escala** | tour de precificação; LTV < 3× CAC |
| Concorrência / moat | quem você desloca + barreira (rede, dados, switching) | "não temos concorrentes" |
| Time | por que ESTE time vence ESTE problema (complementaridade) | currículos; fundadores idênticos, ninguém que venda |
| Ask | capital → **marcos** ("X p/ chegar a Y de ARR") | "precisamos de dinheiro p/ crescer" |
| Visão / retorno | justificar o upside | misturar visão com fantasia |

**Cadeia** — amarre numa história só: `why now → problema → insight → produto →
tração → escala → retorno`. A incoerência que mais derruba: mercado gigante **+ tração
nula** sem explicação; ambição de foguete **+ time sem o skill crítico** (time técnico,
problema comercial, ninguém que venda); GTM braçal que o unit economics grita não escalar.

**Língua** — troque vago por **métrica/tração concreta** (números são do caso — não
invente):
- "temos um mercado enorme" → "TAM bottom-up: ~180 mil empresas no ICP × ticket de
  R$ 30k/ano"
- "estamos crescendo muito" → "MRR +18% mês a mês nos últimos 8 meses (R$ 10k → R$ 25k)"
- "os clientes adoram" → "82% ativos após 12 meses; NRR de 115%"
- "temos o melhor time" → "os fundadores já escalaram produto neste mesmo segmento"
- "temos tecnologia proprietária" → "o desempenho depende de uma base de dados acumulada
  em 3 anos, indisponível publicamente"

Termos **investor-fluent** (retenção, NRR, CAC payback, LTV:CAC, cohort, ARR,
defensibilidade) só funcionam **ancorados em fato**; soltos, viram jargão.

## Heurísticas de aprovação  <!-- → avaliador de pitch (ComplianceMonitor em mode=pitch; Critic fica estreito) -->

- **O peso muda por estágio:** pre-seed/anjo = **time + visão + founder-market-fit**;
  seed = **tração/PMF + mercado + time**; Série A = **unit economics + crescimento +
  retenção**. *Quanto mais tarde, menos narrativa e mais números.*
- **O investidor lidera a rodada no FOMO:** momentum (MRR subindo todo mês), fundador
  excepcional, mercado em inflexão, sinal de que **acontece com ou sem ele**. "Só
  sobrevivemos se você investir" → *pass*.
- **Compra upside assimétrico, não previsibilidade** — ambição "grande demais" é virtude
  aqui (o oposto do crédito).
- **Tração real** (receita, retenção, recorrência, expansão) **>> vanity** (downloads,
  seguidores, mídia espontânea).

## Anti-padrões / red flags  <!-- → avaliador de pitch -->

Instant kills (sinalize forte):
- **Mercado pequeno** (nada corrige mercado pequeno).
- **"Não temos concorrentes"** — lê como "não há mercado" ou "não pesquisou" (o
  concorrente pode ser o Excel, mas existe).
- **"Só precisamos de 1% do mercado"** (jargão vazio sem *math*).
- **Projeções fantasiosas sem lastro** (*"trust me" model*).
- **Fundador que não sabe os próprios números** (CAC, churn, runway) → não controla o negócio.
- **Time sem complementaridade** (todos iguais; ninguém que venda).
- **Cap table quebrado** (sócio que saiu / anjo passivo com fatia grande) buscando rodada.
- *Vanity metrics*; slide 1 sobrecarregado (se não se entende em 30s, fecha).

Erros que **bons fundadores TÉCNICOS** cometem:
- **Pitchar produto/demo** em vez de negócio (detalhar a arquitetura e esquecer como
  cobra). O investidor não investe em código — investe em **negócio habilitado por código**.
- **Enterrar a tração** (o dado mais forte no slide 18) — ponha o bom material na frente.
- **Humildade excessiva** ("é só um começo") onde o investidor quer **ambição lastreada**.
- **Escrever como edital/fomento** (risco + metodologia) em vez de **oportunidade +
  escala** — o erro mais comum de quem vem de subvenção/crédito.

Termos amadores: "o Uber/Airbnb de X", "disruptivo", "game changer", "revolucionário",
"líder global", "sem concorrentes", "mercado de US$ 1 tri" sem ICP.

---
**Fato (NÃO entra aqui — é dado do deal/empresa, NÃO vem de RAG de fomento):**
valuation, % ofertado, instrumento (SAFE/nota conversível/equity), cap table, métricas
reais (MRR, churn, CAC/LTV, runway, ARR, burn), faturamento histórico, segmento/ICP
exato. *São dados do caso, não técnicas de persuasão — não se inventam (se faltam, peça
ao usuário; nunca placeholder).* Confusão comum: tratar o **valuation como argumento de
venda** — é fato a negociar, não craft de pitch. *Regra de bolso: o craft (arco,
why-now, TAM bottom-up, narrativa de tração, posicionamento de time, leitura da tese)
sobrevive à troca de empresa; o número, não. Overlay por tipo de investidor/estágio
(anjo · seed · VC · CVC · tese setorial) fica para quando houver demanda.*
