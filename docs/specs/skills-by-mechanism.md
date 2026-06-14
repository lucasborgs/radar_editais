# Playbooks: conhecimento tácito por mecanismo (+ overlay de fonte)

**Origem:** revisão de prompts & skills (2026-06-13) + consulta cruzada a modelos
externos (2026-06-14). Substitui o esboço inicial "skills por mecanismo".
**Validação:** testes (resolução) + shadow de injeção + métrica de cobertura web ·
**Esforço:** médio (código) + alto (conteúdo de domínio, incremental).

## Princípio central (a decisão mais importante do subsistema)

Separar **verdade normativa** de **conhecimento tácito**. É isso que impede o
produto de virar um amontoado de prompts envelhecendo e passando regra errada.

| Camada | Conteúdo | Fonte da verdade | Quem entrega |
|---|---|---|---|
| **Normativo** | prazos, contrapartida %, rubricas, TRL exigido, documentos, vedações escritas | o **edital** | RAG (`search_edital`) |
| **Tácito (playbook)** | praxe da agência, red flags não-escritos, tom que aprova, anti-padrões, padrões de escrita | conhecimento **curado** | este subsistema |

Regra dura **está no edital** → curar isso num markdown paralelo cria inferno de
manutenção e faz a skill **contradizer o edital vigente** (o LLM tenta reconciliar
e alucina). O fosso competitivo é o **meta-jogo** do fomento — o que não se infere
lendo o PDF. Ex.: *"FINEP aceita TRL 3 no papel, mas subvenção abaixo de TRL 4
reprova na triagem técnica por risco; foque o texto em viabilidade comercial."*

→ **Renomeado de "compliance skill" para "playbook"** (heurística, não norma).

## Problema (o que estava errado)

1. **Eixo de keying errado.** Skills eram `skills/<source>_compliance.md` (por
   fonte). Mas a aderência gruda no **mecanismo** (instrumento), não na agência.
   Quebra em: (a) web é balde de agências, não mecanismo; (b) uma fonte tem vários
   mecanismos (FINEP faz subvenção E crédito); (c) pitch não é compliance.
2. **Conteúdo errado.** A skill misturava regra dura (que é do edital/RAG) com
   tácito. A regra dura cria o risco de contradição descrito acima.

## Estado atual (código)

- `load_skill(source, type)` resolve `skills/<source>_<type>.md` por fonte
  ([skills.py:32](../../core/skills.py)); guard de PLACEHOLDER já trata
  scaffolding como ausente.
- `mechanism` **já é campo estruturado** de todo edital — `MATCH_FIELDS`
  ([wiki_schema.py:46](../../core/kg/wiki_schema.py)), extraído no Stage 1.
- **Distribuição real (41 editais):** `subvencao`=13, `investimento`=5, `None`=23
  (**todos os 21 da web** + 2 FINEP). Vocabulário limpo onde existe; **web 100%
  vazio** — o extrator da Descoberta ([opportunity_discovery._extract](../../core/opportunity_discovery.py))
  não preenche `mechanism`.
- **Consumidores já existem (chave do desenho):** Redator (gera) ·
  ComplianceMonitor (paralelo, advisory, **já carrega skill** —
  [compliance_monitor.py:107](../../core/compliance_monitor.py)) · Critic (gate
  duro no save, **só contradição**, intencionalmente estreito —
  [critic_agent.py:32](../../core/llm/agent_tools/critic_agent.py)) · Checklist (3
  passes). A tool `load_skill` do Redator (spec 05) é o caminho de pull.

## Mudança proposta

### A. Playbook tácito, seções-nomeadas (seções = tipos = roteamento)

O arquivo é markdown; **os cabeçalhos `##` são os tipos**, e o tipo decide qual
consumidor recebe a seção. Sem YAML/parser por ora (markdown serve 6-12 meses; o
tipado estruturado é gatilho de escala).

```
## Padrões de escrita e tom     → Redator (geração; leve)
## Heurísticas de aprovação      → ComplianceMonitor (avaliação)
## Anti-padrões (red flags)      → ComplianceMonitor (avaliação)
## Praxe da agência              → overlay de fonte (qualquer consumidor)
# (NÃO existe seção de regra dura — isso é RAG do edital)
```

**Roteamento nos consumidores existentes — não há agente novo:**
- O **Redator** recebe só `Padrões de escrita e tom` (tira peso do prompt de
  geração, que hoje tem 72 linhas e mede over-fabricação — ver §Grounding).
- O **ComplianceMonitor** vira o "burocrata sênior": recebe `Heurísticas` +
  `Anti-padrões`. É o lugar certo porque já é advisory e já carrega skill.
- O **Critic permanece intocado** — gate duro de contradição. Enfiar heurística
  nele quebraria seu contrato (passaria a bloquear por não-contradição).

### B. Resolução em 3 camadas, composta por seção

```
playbook_efetivo = mechanism/<mech>.md         (base reusável)
                 + source/<source>/global.md   (praxe da agência, todo mecanismo)
                 + source/<source>/<mech>.md    (praxe agência × mecanismo)
```

Merge **por seção** (concatena listas dentro de cada `##`), não prosa solta — o que
neutraliza o risco de instrução conflitante. Layout:

```
skills/
  mechanism/{subvencao,credito,bolsa,matching,equity,premio}.md
  source/finep/{global,subvencao,credito}.md
  source/fapesp/{global,subvencao,bolsa}.md
```

### C. Dependência de dados

Estender `opportunity_discovery._extract` para classificar `mechanism` no
vocabulário canônico (senão web continua sem playbook). Normalização num único
ponto (mapa de sinônimos → slug).

## Decisões travadas

| # | Decisão | Veredito | Por quê |
|---|---|---|---|
| **D1** | Vocabulário | Fechado: `subvencao, credito, bolsa, matching, equity, premio, outro`(escape). Normalização por mapa de sinônimos na extração. | Previsibilidade de roteamento > granularidade jurídica. |
| **D2** | "investimento" | **Matar** → `credito` (dívida reembolsável) + `equity` (FIP/VC). Migrar os 5 FINEP. | Lógica de dívida (garantias) ≠ venture (exit/TAM); termo ambíguo gera dívida semântica. |
| **D3** | `mechanism=None` | **Base tácita genérica + marcador `CONFIANÇA: BAIXA`. NÃO bloquear.** Reavaliar só se o resíduo-None continuar alto após D7. | Web é 100% None hoje; bloquear mata o Redator pra todo o volume. Sob o modelo tácito, base genérica é segura (não afirma regra falsa). |
| **D4** | Pitch | **Skill `equity`, mas roteada ao agente de pitch**, não ao caminho de compliance do edital. | Pitch é gênero outbound (fit com tese), não aderência. Concilia "é skill" × "não é compliance". |
| **D5** | Fonte standalone | **3 camadas** (`mechanism/base` + `source/global` + `source/mechanism`). | Praxe da agência existe em ambos os níveis; viável sem ambiguidade porque o merge é por-seção. |
| **D6** | Granularidade | **Seções-como-tipos desde já** (cabeçalhos `##`), arquivo monolítico endereçável por seção. YAML estruturado = gatilho de escala. | A seção JÁ é o tipo e o roteamento — colapsa o fatiamento, a tipagem e o D6 num só design. |
| **D7** | Ordem | **Loader + skills de mecanismo primeiro, validado em FINEP/FAPESP** (mechanism limpo); **extrator web cedo/em paralelo**; overlays depois. | Loader é independente e FINEP/FAPESP dão set de validação limpo → não bloqueia por web vazio. Extrator é cedo-não-último porque web é o volume. |

## Roteiro de implementação

1. **Loader novo** (`core/skills.py`): resolve `mechanism` (+ `source`) da wiki
   page e compõe as 3 camadas por seção; expõe seções nomeadas; preserva guard de
   placeholder; fallback `None` → base genérica + marcador.
2. **Vocabulário + normalização** num ponto único; **migração** dos 5 `investimento`.
3. **Skills de mecanismo** (base) para `subvencao` e `credito` primeiro (cobrem os
   18 FINEP+FAPESP indexados); validar roteamento.
4. **Roteamento nos consumidores:** Redator puxa `Padrões de escrita`; Monitor
   passa a carregar `Heurísticas`+`Anti-padrões` (substitui a injeção atual do
   `_compliance.md` inteiro).
5. **Extrator web:** `_extract` classifica `mechanism` (mesmo vocabulário).
6. **Overlays de fonte** (FINEP/FAPESP) enxutos; `finep_compliance.md`/
   `fapesp_compliance.md` migram para overlays só-tácito.
7. Conteúdo de domínio entra **incremental** atrás do guard de placeholder.

## Validação

- **Testes:** resolução compõe 3 camadas por seção; `mechanism=None` → fallback
  sem quebrar; seção ausente é ignorada; roteamento entrega a seção certa a cada
  consumidor; guard de placeholder preservado.
- **Shadow de injeção:** comparar texto injetado (source-keyed antigo vs novo) em
  FINEP/FAPESP — garantir que não se perde heurística útil na migração.
- **Cobertura web:** após o extrator, medir % de web com `mechanism` resolvido
  (hoje 0/21).
- **Conteúdo NÃO é auto-validável** — exige revisão humana (heurística errada é
  pior que ausente; protegida pelo guard de placeholder).

## Conexão com o grounding (thread paralelo)

O prompt do Redator está sobrecarregado (72 linhas) e o eval mede over-fabricação
de fit. **Tirar tácito/regra da geração e mover pro avaliador (Monitor) é fix
plausível parcial** do grounding diagnosticado — separar geração de avaliação
reduz a pressão que faz o agente forjar aderência. Medir no eval de escrita
(quando o gate de grounding ficar confiável — ver BACKLOG).

## Risco

- **Qualidade de `mechanism`** — limpo onde existe, mas LLM-extraído e vazio na
  web. Mitigação: vocabulário canônico + normalização + fallback + extrator (D7).
- **Heurística curada desatualiza** — menor que regra-dura-curada (heurística de
  praxe muda devagar), mas real. Mitigação: revisão humana + guard de placeholder
  + manter regra dura SEMPRE no RAG, nunca no playbook.
- **Migração de FINEP/FAPESP** pode perder conteúdo útil → shadow de injeção.

## Perguntas remanescentes (não bloqueiam o início)

- Mapa de sinônimos exato do vocabulário (refinar com mais dados reais de editais).
- "Burocrata" no Monitor: ampliar o Monitor atual vs um passe dedicado de tácito
  (decidir na etapa 4, com o conteúdo na mão).
- Quando migrar markdown → estruturado (YAML): gatilho de tamanho/consumidores
  distintos (espelha o item 09 da auditoria — revisitar, não antecipar).
