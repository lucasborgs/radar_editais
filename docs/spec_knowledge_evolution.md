# Spec: Evolução do Ecossistema de Conhecimento

**Data:** 2026-06-15 (status atualizado 2026-06-16)
**Status:** Em implementação — ver tabela abaixo
**Branch:** `feat/knowledge-evolution`

## Status de implementação

| Item | Estado | Onde / o que falta |
|------|--------|--------------------|
| **1 — loop de pesos** | ✅ Feito | auto-apply gated + `weight_change_log` (migration 021, commit ed74d1572). Pendência: dedup de sugestão repetida entre ciclos (BACKLOG) |
| **2 — deep_research → staging** | ✅ Feito (Fase B) | `research_findings` (migration 023) + router `research.py` (commit bdab8b82f) |
| **3 — overlays de playbook** | 🟡 Só scaffold | tabela `playbook_overlays` (024) + loader da 4ª camada (leitura) + router. **Falta o job `run_meta_reflection` que aprende/escreve** (BACKLOG) |
| **4 — compressão + extração de sinal** | ✅ Feito (na compressão) | `extract_session_signal` (migration 022, commit d8cceaabe). **Fase B "ao vivo" = proposta nova, ver no Item 4** |
| **5 — Critic sub-agente** | ✅ Feito | 3 tools + `max_steps=3` (commit e9595c290) |
| **6 — deprecação (`forget`)** | 🔵 Proposto | ver abaixo |
| **7 — rastro do orquestrador humano** | 🔵 Proposto | nova frente, ver abaixo |

**Pendência transversal:** migrations 021–024 ainda não aplicadas no Supabase remoto (BACKLOG — deploy).

## Princípio transversal

*Autonomia com observabilidade.* O sistema age, mas todo ato autônomo é logado, reversível, e
surfaçado numa review feed. "AI crafts, Human decides" vira "AI acts, Human can correct."

Isso se aplica especificamente a: pesos de matching, playbooks (learned overlays), e
reflection_insights. Para `deep_research → library`, o gate humano permanece — só a
fricção do gate é reduzida (staging area + um clique).

---

## Item 1 — Fechamento do loop de pesos

**Contexto:** `reflection_service.py` gera `weight_suggestions` com confidence level mas nunca
aplica. O humano precisa editar `matching_weights` manualmente.

**Decisão:** Aplicação automática condicionada a três guardas simultâneos:
- `confidence == "high"`
- `outcomes_considered >= 5`
- `|delta| <= 5` por dimensão por ciclo

Toda aplicação cria linha em `weight_change_log` (rationale, delta, confidence,
outcomes_window). Feed de auditoria na UI com botão de reverter.

**Consequências:**
- Positivo: loop fecha sem fricção para casos claros
- Risco: confidence é auto-reportada pelo mesmo LLM — sem validação cruzada.
  Mitigação: cap de delta + reversibilidade eliminam lock-in
- Evolutivo: após N ciclos, comparar performance do match antes/depois como sinal real

**Plano:**
1. Verificar o que `core/weight_approval.py` já faz (scaffolding existente)
2. Migration: tabela `weight_change_log`
3. Modificar `reflect_workspace` para chamar aprovação condicional no final
4. Endpoint `GET /me/weight-changes` para feed de auditoria
5. UI: banner "X dimensões ajustadas automaticamente" com link para log

---

## Item 2 — `deep_research` → memória permanente (Fase B)

**Contexto:** Sub-agente descobre fatos com fontes, tudo morre no turno. Gate humano
permanece — o que falta é construir a staging area.

**Decisão:** Tabela `research_findings` onde resultados chegam automaticamente, tagueados
`source="deep_research"` e `verified=false`. Usuário vê fila de findings pendentes e decide
o que promover para `content_library`.

Guardrails: TTL de 30 dias para findings não revisados; limite de N findings pendentes por
workspace antes de o `deep_research` parar de criar novos.

**Plano:**
1. Migration: `research_findings` (workspace_id, question, answer, sources, query,
   created_at, reviewed_at, promoted_to_library_id)
2. Modificar tool `deep_research` para inserir em `research_findings` além de retornar string
3. Endpoints `GET /research-findings` e `POST /research-findings/{id}/promote`
4. UI: fila no painel de library com badge "não verificado"

---

## Item 3 — Aprendizado inter-workspace (destilação de playbooks)

**Contexto:** Insights ficam isolados por `workspace_id`. Padrões cross-tenant nunca
enriquecem os playbooks.

**Decisão:** Dois componentes:

**A) Meta-reflexão cross-tenant** (job periódico): agrega `application_log` anonimizado
por `(mechanism, source)`, roda reflexão LLM, gera padrões de nível 3 (cross-workspace).

**B) Learned overlays no banco**: tabela `playbook_overlays`
`(mechanism, source, section, body, confidence, origin)`. O loader de `core/skills.py`
faz merge em 4 camadas:

```
base git (mechanism/*.md)
  + source git (source/<fonte>/global.md + source/<fonte>/<mech>.md)
  + learned overlay do banco  ← o sistema escreve aqui
  + overlay deste workspace   ← futuro
```

Os arquivos `.md` em git continuam sendo o seed canônico humano; o banco é onde o
sistema aprende. Learned overlays adicionam por seção, nunca sobrescrevem o arquivo todo.

**Consequências:**
- Positivo: playbooks evoluem com evidência real sem PR humano
- Risco: learned overlays podem contradizer o seed. Mitigação: UI de "playbook atual"
  mostra o que veio de onde (camada por camada)
- Dependência: precisa de volume cross-tenant — threshold configurável por (mechanism, source)

**Plano:**
1. Migration: `playbook_overlays` + `meta_reflection_runs`
2. Job `run_meta_reflection` (semanal): agrega outcomes anonimizados, chama LLM, escreve overlays
3. Modificar `load_playbook` em `core/skills.py` para consultar `playbook_overlays` como 4ª camada
4. Endpoint `GET /playbooks/{mechanism}/layers` — playbook resolvido camada por camada (auditoria)
5. UI: indicador "X overlays aprendidos" no painel de configurações

---

## Item 4 — Compressão episódica com extração de sinal

**Contexto:** Ao comprimir sessões longas (>10 turnos), o que falhou no processo — rejeições
do Critic, correções do usuário — se perde na narrativa.

**Decisão:** Dois prompts separados ao comprimir, rodando em paralelo:

**Prompt A (existente, refinado):** compressão narrativa → `writing_sessions.summary`.
Alimenta o próximo turno. Captura "o que foi dito".

**Prompt B (novo):** extração de sinal estruturado → `reflection_insights`. Extrai:
- Seções que o Critic rejeitou + número de iterações até aprovar
- Afirmações que o usuário corrigiu explicitamente
- Seções que fluíram sem atrito (sinal positivo)

Saída JSON no schema de `reflection_insights` (level=1), marcado `origin="episodic_compression"`.

O passo de "extrator de por que falhou" colapsa dentro da compressão — mesmo momento, dois propósitos.

**Plano:**
1. Implementar `extract_session_signal(turns, session_id, workspace_id)` → lista de insights
2. Chamar em paralelo com compressão narrativa (`asyncio.gather`)
3. Inserir em `reflection_insights` com `origin="episodic_compression"`
4. Rodar também em `POST /writing/{id}/close` para sessões abaixo do threshold de compressão

**Fase B — extração ao vivo (proposta 2026-06-16):** hoje o sinal só é extraído na
compressão (≥10 turnos) ou no close. Mas a reflexão *intra-run* (o `_REFLECT_PROMPT` no
`agent_runtime`) já produz a síntese mais rica do agente no meio do loop — e ela é descartada
com o turno. Capturá-la é quase de graça: o texto já existe no `messages[]`.

- **Demux obrigatório:** a reflexão mistura "o que aprendi" (observação de instância →
  episódica) com "o que ainda falta" (estado de tarefa → memória de trabalho, morre certo).
  Só a primeira parte é persistida.
- **Mesmo extrator, gatilho diferente:** reusar `extract_session_signal` num novo
  `extract_intra_run_signal`, disparado pelos **mesmos sinais leves** que já acionam a reflexão
  dinâmica (erro de tool, mudança de plano via `write_todos`, output acumulado). Não persiste
  rodada trivial.
- **Destino é episódica, não procedural:** grava em `reflection_insights` com
  `origin="intra_run"`, level=1, **tagueado por `mechanism`**. Vira procedural só depois,
  via a meta-reflexão do Item 3 (n=1 → overlay seria overfitting). A tag de `mechanism` é a
  ponte que liga esta captura ao Item 3.

---

## Item 5 — Critic como sub-agente

**Contexto:** Critic atual é 1-shot. Faz um único retrieve (primeiros 500 chars do rascunho
como query) e decide. Não tem acesso ao `CompanyProfile` — não detecta elegibilidade incorreta.

**Decisão:** Refatorar `run_critic` para usar `run_subagent` com 3 tools e `max_steps=3`:

| Tool | Função |
|------|--------|
| `search_edital(query)` | Retrieve RAG dirigido — Critic escolhe a query |
| `read_company_profile()` | Retorna CompanyProfile serializado → checagem de elegibilidade |
| `read_proposal_sections()` | Retorna outras seções já redigidas |

Contrato preservado: **só bloqueia por contradição, nunca por omissão, na dúvida aprove.**
Temperature: 0.05. Falha graciosa: erro no sub-agente → `CriticResult(approved=True)`.

**Consequências:**
- Positivo: pega contradições que retrieve inicial perdia; detecta elegibilidade incorreta (novo)
- Risco: mais raciocínio pode gerar falsos positivos. Mitigação: temperatura mínima + instrução
  reforçada + max_steps baixo limita exploração
- Observabilidade: logar steps count por decisão — média > 1.5 retrieves indica retrieve inicial fraco

**Plano:**
1. Criar `build_critic_tools(session)` com as 3 tools
2. Refatorar `run_critic` para usar `run_subagent(name="critic", max_steps=3, ...)`
3. Adicionar `CompanyProfile` via tool `read_company_profile`
4. Logar `steps` count por decisão para calibração futura

---

## Item 6 — Deprecação de conhecimento (o `forget` explícito)

**Contexto:** Os Itens 1–5 cobrem `remember` (escrita: findings, overlays, insights), `recall`
(RAG, KG, merge de playbook) e `improve` (pesos, meta-reflexão, Critic). Mas os únicos
mecanismos de esquecimento atuais agem só na *borda*: TTL de findings **não revisados**
(Item 2), revert de pesos (Item 1) e `archived_at` manual da `content_library`. Nada deprecia
conhecimento que **entrou, foi válido, e apodreceu**. Em domínio com prazo (editais expiram)
e numa camada de aprendizado append-only, isso é dívida que cresce sozinha: o sistema aprende
mas quase não esquece.

**Decisão:** Esquecimento por evidência e por expiração, em três frentes — mantendo tudo
auditável e reversível (nunca DELETE; sempre soft-deprecate com timestamp + origin).

**A) Domínio expira (liga com o ETL incremental):** quando o refresh incremental detecta
edital encerrado/removido, marcar `content_library` items e `edital_chunks` derivados com
`deprecated_at` + `deprecated_reason`. Excluídos do `recall` por padrão (filtro no retriever),
mantidos para auditoria. Promoções de `research_findings` herdam o vínculo ao edital de origem
para serem deprecadas junto.

**B) Overlays apodrecem por evidência:** `playbook_overlays` ganha decaimento — um overlay
contradito por outcomes recentes tem `confidence` reduzida; abaixo do threshold de merge ele
sai do `load_playbook` sem ser apagado (campo `retired_at` + origin do retiro). Aposentadoria
por evidência, não por PR humano — coerente com "AI acts, Human can correct".

**C) Insights decaem na janela:** a meta-reflexão (Item 3A) passa a ponderar `reflection_insights`
por recência (half-life ou janela deslizante configurável), para evidência velha não dominar
padrões de nível 3.

**Consequências:**
- Positivo: `recall` para de servir ruído expirado; merge de playbook não vira sedimento;
  meta-reflexão reflete o presente
- Risco: deprecar cedo demais perde conhecimento ainda útil. Mitigação: soft-deprecate +
  reversibilidade + `recall` pode opt-in incluir deprecados para auditoria
- Risco: decaimento de overlay/insight é mais um knob a calibrar. Mitigação: threshold e
  half-life configuráveis por (mechanism, source), começando conservadores
- Dependência: frente A precisa do ETL incremental detectando encerramento; B/C dependem de
  volume de outcomes (após Item 3/4 consolidados)

**Plano:**
1. Migration: `deprecated_at` + `deprecated_reason` em `content_library` e `edital_chunks`;
   `retired_at` + `retired_reason` em `playbook_overlays`
2. Hook no refresh incremental: ao marcar edital encerrado, propagar `deprecated_at` para
   chunks e library items vinculados
3. Filtro padrão `deprecated_at IS NULL` no retriever e no `load_playbook` (4ª camada)
4. Decaimento de overlay: no `run_meta_reflection`, reduzir `confidence` de overlays
   contraditos por outcomes recentes; aposentar abaixo do threshold
5. Ponderação por recência dos `reflection_insights` na agregação da meta-reflexão
6. Endpoint/UI: incluir itens deprecados/aposentados nos feeds de auditoria com motivo

---

## Item 7 — Rastro do orquestrador humano (proposta 2026-06-16)

**Contexto:** o orquestrador do ecossistema é o humano (Loop A da arquitetura de memória):
ele roteia (escolhe edital, library items, escopo) e *integra* (conecta o raciocínio entre os
especialistas). Mas as decisões dele viram **ações** — uma WritingSession nasce, um peso muda —
e o **porquê** evapora. O sistema vê o *quê*, nunca o *motivo*. É a mesma amnésia da reflexão
intra-run (Item 4 Fase B), só que na escala macro.

**Decisão:** capturar uma fração do raciocínio de roteamento, sem transformar o humano em
formulário. Campo opcional "por quê?" (uma linha) nos momentos de orquestração de maior valor:
início de WritingSession, escolha de escopo análogo, promoção de finding. Persistido como
`reflection_insights` com `origin="human_routing"`, level=2, tagueado por edital + `mechanism`.

- **Opt-in, nunca bloqueante:** vazio é o default; jamais trava o fluxo.
- **Mesmo cano:** reaproveita `reflection_insights` e a meta-reflexão do Item 3 — o "porquê"
  humano é evidência de altíssima qualidade para padrões de nível 3.
- **Coerência Grantable:** o humano segue sendo o orquestrador; só passa a *deixar rastro*.

**Consequências:**
- Positivo: o único raciocínio cross-domínio do ecossistema deixa de ser descartado
- Risco: fricção / campos ignorados. Mitigação: opt-in, uma linha, só nos pontos de maior valor
- Evolutivo: com volume, o sistema pode *sugerir* o roteamento e medir contra a escolha humana

**Plano:**
1. Campo opcional `rationale` nos endpoints de orquestração (writing/start, escopo, promote)
2. Persistir em `reflection_insights` com `origin="human_routing"` + tags
3. Incluir na agregação da meta-reflexão (Item 3A) como evidência ponderada

---

## Dependências e ordem de implementação

```
Item 4 (compressão + sinal)
    └→ alimenta Item 1 (pesos) com mais evidência
    └→ alimenta Item 3 (cross-workspace) com insights mais ricos

Item 1 (pesos)           ← pode ser feito agora, infraestrutura existe (weight_approval.py)
Item 5 (Critic)          ← independente, arquiteturalmente isolado
Item 2 (deep_research)   ← independente, nova tabela
Item 3 (cross-workspace) ← depende de volume; após Item 4 consolidado

Item 6 (forget)
    frente A (domínio)     ← depende do ETL incremental detectar encerramento; independente do resto
    frente B (overlays)    ← depende do Item 3 (playbook_overlays + run_meta_reflection)
    frente C (insights)    ← depende do Item 3A (meta-reflexão) e do volume do Item 4

Item 4 Fase B (ao vivo)   ← independente; reusa extract_session_signal. Alimenta Item 1 e Item 3
Item 7 (rastro humano)    ← independente; reusa reflection_insights. Alimenta Item 3A
```

**Sprint sugerida:**
- Sprint 1: Item 1 + Item 5 (menores, impacto imediato)
- Sprint 2: Item 4 (fundação do aprendizado)
- Sprint 3: Item 2 (Fase B do deep_research) + Item 6A (domínio expira — pareia com o ETL incremental)
- Sprint 4: Item 3 (cross-workspace) + Item 6B/6C (decaimento — junto da infra de overlays/meta-reflexão que habilitam)
